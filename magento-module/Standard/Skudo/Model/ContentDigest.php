<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

/**
 * El esquema de particionado y el digest de CONTENIDO que `/checksums`
 * publica y que `reconcile()` (lado Python,
 * `src/skudo/ingest/reconcile.py`) recalcula sobre el espejo.
 *
 * Por qué existe (H1). Hasta acá la reconciliación comparaba el CONJUNTO de
 * SKUs y una huella de ese conjunto. Eso detecta un producto creado o
 * borrado, y no detecta —nunca, ni al décimo ciclo, ni al año— que un VALOR
 * de un SKU existente cambió y el espejo no se enteró. El spec §2 parte de
 * que el registrador del tenant reescribe productos existentes de forma
 * continua y el §7.6 declara la detección de regresión obligatoria, así que
 * un espejo que puede sostener un valor rancio indefinidamente invalida todo
 * lo que se calcule encima.
 *
 * QUÉ ENTRA EN EL DIGEST: el par `(sku, updated_at)`. No los valores EAV:
 * traerlos para 228.881 productos × 1.066 atributos en un endpoint de
 * reconciliación sería más caro que re-sincronizar. `updated_at` es la
 * versión del contenido que Magento ya mantiene, y la medición de H1 sobre
 * la instancia de desarrollo dice hasta dónde alcanza:
 *
 *   camino de escritura                          | ¿mueve updated_at?
 *   ---------------------------------------------|-------------------
 *   ProductRepository::save() (modelo)           | SÍ
 *   Product\Action::updateAttributes() (masiva)  | SÍ (lo escribe explícito)
 *   CatalogImportExport (importación por lotes)  | SÍ (lo escribe explícito)
 *   UPDATE de SQL directo a catalog_product_entity | SÍ (ON UPDATE CURRENT_TIMESTAMP)
 *   Product\Action::updateWebsites()             | NO
 *   UPDATE de SQL directo a una tabla EAV        | NO
 *
 * Las dos últimas filas son el límite declarado de este digest, y no un
 * descuido: el cambio de websites lo cubre el observer
 * `Observer\ProductBulkChanged` (es la razón por la que ese observer sigue
 * haciendo falta aunque exista este digest), y un `UPDATE` de SQL directo a
 * una tabla satélite (valores EAV, categorías, websites) no deja rastro en
 * ninguna parte del esquema de Magento: es el piso del sistema, y conocerlo
 * acota el diseño en vez de fingir que no está.
 *
 * POR QUÉ PARTICIONADO. El remedio de una huella global es "re-sincronizá
 * todo", que en 228.881 productos son horas y es la MISMA respuesta si
 * derivó un producto o si derivaron todos. Partido, el resultado dice
 * DÓNDE: una partición divergente de 256 es ~900 productos en el catálogo
 * piloto, un remedio dirigido, y además señala en qué cohorte se está
 * portando mal el proceso de aguas arriba.
 *
 * EL ESQUEMA, y por qué es reproducible idéntico en los dos lados:
 *
 *   partición(sku) = los dos primeros caracteres hex de sha256(bytes UTF-8 del sku)
 *
 * Depende SÓLO de los bytes del SKU. No de ids de entidad (que el espejo no
 * guarda), no del orden de ninguna consulta, no de la colación de MySQL
 * (verificada `utf8mb4_general_ci` en `sku`, que ordena sin distinguir
 * mayúsculas ni acentos: por eso el Ruling 1 prohíbe el ORDER BY de SQL), no
 * de la zona horaria, no del particionamiento físico de ninguna tabla.
 * `hash('sha256', $sku)` de PHP y `hashlib.sha256(sku.encode('utf-8'))` de
 * Python dan la misma cadena hex para los mismos bytes, y el SKU viaja como
 * texto sin normalizar por todo el sistema (verificado sobre HTTP real con
 * `SKU-ÑOÑO`, ida y vuelta intacto). 256 particiones es una constante y no
 * un parámetro a propósito: un número configurable es una forma de que los
 * dos lados particionen distinto y comparen manzanas con naranjas. Viaja en
 * el payload (`partition_count`) para que el lado Python pueda EXIGIR el
 * acuerdo y fallar ruidosamente si algún día no lo hay, en vez de reportar
 * "sin deriva" sobre una comparación sin sentido.
 *
 * EL DIGEST DE CADA PARTICIÓN:
 *
 *   sha256( implode("\n", sort_string( ["{sku}\t{token}" por cada fila] )) )
 *
 * Mismo criterio de orden que `sku_digest` (Ruling 1): se ordena EN PHP con
 * `SORT_STRING`, que compara byte a byte igual que el `sorted()` de Python
 * sobre UTF-8, nunca con un `ORDER BY` de SQL.
 *
 * EL TOKEN DE `updated_at`, que es donde este mecanismo tenía su trampa. Los
 * dos lados tienen que coincidir en la forma TEXTUAL exacta del timestamp, y
 * el espejo no guarda el texto: guarda un `timestamptz` de Postgres al que
 * llegó parseando el texto de Magento. Si los dos lados escribieran ese
 * texto con reglas distintas —una fracción de segundo, un desplazamiento de
 * zona, un `0000-00-00` interpretado de dos formas— el digest reportaría
 * deriva PERMANENTE y el remedio que prescribe no la limpiaría nunca: es
 * exactamente el modo de fallo del filtro de versión (C2) y del ORDER BY
 * (Ruling 1). Así que la canonicalización es una sola regla, escrita una vez
 * acá y replicada literalmente en `reconcile.py`:
 *
 *   - `YYYY-MM-DD HH:MM:SS` (los primeros 19 caracteres, si eso es lo que
 *     hay): se usa tal cual. Una fracción de segundo se DESCARTA en vez de
 *     desempatar: la columna de Magento es `timestamp` sin precisión
 *     fraccionaria, y si un tenant la alteró, coincidir al segundo es mejor
 *     que discrepar para siempre.
 *   - vacío, `0000-00-00 ...` (que MySQL admite y los catálogos heredados
 *     contienen) o cualquier cosa que no tenga esa forma: el token literal
 *     `desconocido`. NO una fecha de relleno, que sería un dato falso con
 *     aspecto confiable, y no la cadena vacía, que se confundiría con "no
 *     vino el campo". El token no puede colisionar con ningún timestamp.
 */
class ContentDigest
{
    /**
     * Número de particiones. CONSTANTE, no configuración: ver el docblock.
     * Debe coincidir con `PARTITION_COUNT` de `src/skudo/ingest/reconcile.py`.
     */
    public const PARTITION_COUNT = 256;

    /**
     * Token de un `updated_at` que no se puede leer. Idéntico al del lado
     * Python (`reconcile.UNKNOWN_TIMESTAMP_TOKEN`).
     */
    public const UNKNOWN_TIMESTAMP = 'desconocido';

    /** Separador dentro de una línea. El SKU no lo contiene en la práctica. */
    private const FIELD_SEPARATOR = "\t";

    private const LINE_SEPARATOR = "\n";

    /**
     * Partición de un SKU: los dos primeros caracteres hex de su sha256.
     * 256 valores, '00'..'ff', en minúsculas (que es lo que emiten tanto
     * `hash()` de PHP como `hexdigest()` de Python).
     */
    public function partitionOf(string $sku): string
    {
        return substr(hash('sha256', $sku), 0, 2);
    }

    /**
     * Forma canónica del `updated_at` de Magento, o el token de desconocido.
     */
    public function timestampToken(?string $raw): string
    {
        if ($raw === null) {
            return self::UNKNOWN_TIMESTAMP;
        }

        $candidate = trim($raw);
        if (str_starts_with($candidate, '0000-00-00')) {
            return self::UNKNOWN_TIMESTAMP;
        }
        if (preg_match('/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/', $candidate, $m) !== 1) {
            return self::UNKNOWN_TIMESTAMP;
        }

        return $m[1];
    }

    /**
     * Digest de contenido por partición.
     *
     * Devuelve una LISTA de objetos y no un mapa partición => digest a
     * propósito: un mapa PHP con claves como '00', '10', 'ff' es un array con
     * claves mixtas (PHP convierte '10' en int y deja '00' como string), y
     * `json_encode` de eso es impredecible entre objeto y array — el bug que
     * ya mordió tres veces en este módulo (`labels`, `global_values`,
     * `store_values`). Una lista no tiene ese problema en ninguna forma.
     *
     * Sólo se emiten las particiones NO vacías: 256 filas con conteo cero no
     * dicen nada que la ausencia no diga, y el lado Python une los dos
     * conjuntos de claves antes de comparar, así que una partición que existe
     * de un solo lado se reporta como divergente.
     *
     * @param iterable<array{sku: string, updated_at: string|null}> $rows
     * @return list<array{partition: string, product_count: int, content_digest: string}>
     */
    public function partitions(iterable $rows): array
    {
        /** @var array<string, list<string>> $lines */
        $lines = [];
        foreach ($rows as $row) {
            $sku = (string) $row['sku'];
            $partition = $this->partitionOf($sku);
            $lines[$partition][] = $sku
                . self::FIELD_SEPARATOR
                . $this->timestampToken($row['updated_at'] ?? null);
        }

        // Las claves de partición también se ordenan en PHP con SORT_STRING
        // (ksort): son hex en minúsculas, así que el orden es el mismo que el
        // de `sorted()` en Python, y una respuesta con orden estable es más
        // fácil de diffear a mano cuando alguien investiga una divergencia.
        ksort($lines, SORT_STRING);

        $out = [];
        foreach ($lines as $partition => $partitionLines) {
            sort($partitionLines, SORT_STRING);
            $out[] = [
                'partition' => (string) $partition,
                'product_count' => count($partitionLines),
                'content_digest' => hash('sha256', implode(self::LINE_SEPARATOR, $partitionLines)),
            ];
        }

        return $out;
    }
}
