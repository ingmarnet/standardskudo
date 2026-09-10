<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\Exception\InputException;
use Standard\Skudo\Api\ChecksumReaderInterface;

/**
 * S0 Task 13: la red de seguridad de todo el sub-proyecto. Un delta puede
 * perderse (una cola truncada, un observer que no disparó en una importación
 * masiva por SQL directo, un reintento fallido) y, sin reconciliación, el
 * espejo se desvía en silencio: todos los scores de S1 heredan ese error sin
 * que nadie lo note. Este lector responde las preguntas que `reconcile()`
 * (lado Python, `src/skudo/ingest/reconcile.py`) necesita para decidir si el
 * espejo coincide con Magento: cuántos SKUs activos hay, una huella (digest)
 * de cuáles son —para detectar el caso en que el conteo coincide mientras el
 * conjunto no (un SKU borrado y otro creado en el mismo intervalo)— y, desde
 * H1, un digest de CONTENIDO por partición.
 *
 * Ruling 5 (H1): el conteo y la huella del CONJUNTO de SKUs no detectan un
 * VALOR cambiado en un SKU existente. Nunca: no es una carrera que se
 * resuelva al siguiente ciclo, es un punto ciego permanente, y el docblock
 * anterior de esta clase lo decía sin sacar la conclusión ("detecta un SKU
 * borrado y otro creado"). Como el spec §2 parte de que el registrador del
 * tenant reescribe productos existentes de forma continua y el §7.6 declara
 * obligatoria la detección de regresión, esta clase publica además un digest
 * de `(sku, updated_at)` PARTIDO en 256 particiones por hash del SKU. Ver
 * `Model\ContentDigest` para el esquema, la reproducibilidad en los dos
 * lados y —lo que más importa— el límite declarado de lo que `updated_at`
 * puede ver.
 *
 * Ruling 3 (S0 Task 13, REVISADA sobre HTTP real): esta clase no agrega
 * NINGÚN filtro de versión propio. La versión anterior de esta ruling decía
 * lo contrario —delegaba en `ActiveVersionResolver`, que acotaba por reloj—
 * y era un error: bajo Magento_Staging `catalog_product_entity` sí tiene una
 * fila por VERSIÓN, pero Magento ya acota todo `Select` del framework a la
 * versión APLICADA por su cuenta, y anclar un segundo filtro en el reloj
 * produce un conjunto más estrecho que el que la tienda sirve. Como
 * `/products` sufría el MISMO doble filtro, los dos lados coincidían sobre
 * el conjunto estrechado y `reconcile()` reportaba "sin deriva" para
 * siempre: la red de seguridad quedaba ciega justo al hueco que existe para
 * detectar. Ver `Model\VersioningSchema`.
 *
 * Ruling 4 (S0 Task 13, cross-edition): esta clase no referencia ninguna
 * clase de `Magento\Staging\*` ni de Adobe Commerce, ni decide nada a partir
 * del edition string. Tras quitar el filtro propio no queda ni siquiera una
 * pregunta de esquema que hacer: el mismo código carga igual en Community y
 * en Adobe Commerce, y en cada una devuelve la población que ESA instancia
 * considera activa.
 */
class ChecksumReader implements ChecksumReaderInterface
{
    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly ContentDigest $contentDigest,
        // M3: aunque $storeId no filtre (Ruling 2), tiene que EXISTIR. Un
        // storeId inexistente que devuelve 200 hace que `reconcile()` compare
        // dos lados de acuerdo sobre un espejo equivocado. Ver
        // Model\StoreViewGuard.
        private readonly StoreViewGuard $storeViewGuard,
    ) {
    }

    /**
     * Ruling 2 (S0 Task 13): $storeId se acepta por simetría de interfaz y
     * uso futuro, pero NO filtra. Instrucción anterior (revocada): un
     * conteo/digest limitado a "visible en esa store view" parecía más
     * preciso, pero `full_sync` (lado Python) recorre `iter_products(store_id)`
     * SIN filtro de visibilidad — devuelve todo el catálogo activo para cada
     * store view — así que `mirror_count` es el mismo número para cualquier
     * store view. Si este método filtrara por visibilidad, jamás coincidiría
     * con el espejo y `reconcile()` exigiría un re-sync completo para
     * siempre, sin que ese re-sync pudiera arreglar nada (el remedio no
     * cambia el hecho de que ambos lados miden cosas distintas). Mirrorear el
     * catálogo completo por store view es deliberado: es lo que permite
     * reportar "este producto no aparece en la navegación de BR".
     */
    /**
     * Tope de particiones por llamada. Con 256 particiones en total, pedir
     * los SKUs de más de 32 es pedir un octavo del catálogo por una vía que
     * existe para reparar una cohorte: a partir de ahí la pasada completa es
     * más barata y más simple, y hay que decirlo en vez de servir un payload
     * enorme por un endpoint de reconciliación.
     */
    private const MAX_PARTITIONS = 32;

    public function getChecksums(int $storeId, ?string $partitions = null): array
    {
        $this->storeViewGuard->assertExists($storeId);
        $wanted = $this->parsePartitions($partitions);

        $connection = $this->resource->getConnection();
        $entity = $this->resource->getTableName('catalog_product_entity');

        // Sin where propio: la población es exactamente la que Magento
        // considera activa, la MISMA que devuelve `/products` (Ruling 3).
        //
        // Se trae `updated_at` en la MISMA consulta y no en una segunda: dos
        // consultas sobre un catálogo que cambia mientras se leen darían un
        // conjunto de SKUs y un conjunto de timestamps de instantes distintos,
        // y esa incoherencia se reportaría como deriva del espejo. Es la misma
        // columna, y la misma cadena, que `/products` emite en su campo
        // `updated_at` (`ProductReader::baseEntitySelect()`), que es de donde
        // el espejo obtuvo el valor que va a comparar.
        $select = $connection->select()->from(
            ['e' => $entity],
            ['sku' => 'e.sku', 'updated_at' => 'e.updated_at']
        );

        $rows = $connection->fetchAll($select);
        $skus = array_column($rows, 'sku');

        // Ruling 1 (S0 Task 13): el orden se decide EN PHP, nunca con un
        // ORDER BY de SQL. Bajo la colación típica de MySQL/MariaDB para
        // esta columna (utf8mb4_general_ci), un ORDER BY ordena sin
        // distinguir mayúsculas/minúsculas ni acentos — produce una
        // secuencia distinta de la que da `sorted()` en Python, que ordena
        // por punto de código Unicode. Esa discrepancia haría que
        // sku_digest() (Python) y este método jamás coincidieran para
        // ningún catálogo con SKUs de distinta capitalización o con
        // acentos, y el remedio que reconcile() prescribe ante un digest
        // que no coincide — un re-sync completo — nunca lo repararía: los
        // dos lados seguirían ordenando distinto después del re-sync.
        // sort($skus, SORT_STRING) ordena por bytes de la cadena (el mismo
        // criterio, byte a byte, que sorted() de Python aplica sobre UTF-8),
        // así que ambos lados producen la MISMA secuencia sin necesidad de
        // coordinar colación ni paginación.
        sort($skus, SORT_STRING);

        return WebApiEnvelope::wrap([
            'product_count' => count($skus),
            'sku_digest' => hash('sha256', implode("\n", $skus)),
            // Viaja para que el lado Python pueda EXIGIR el acuerdo del
            // esquema en vez de comparar particiones distintas y reportar
            // "sin deriva" sobre una comparación sin sentido.
            'partition_count' => ContentDigest::PARTITION_COUNT,
            // Lista de objetos, nunca un mapa: ver ContentDigest::partitions().
            'content_partitions' => $this->contentDigest->partitions($rows),
            // H3: los SKUs de las particiones PEDIDAS, para que la
            // reparación de una partición divergente no tenga que recorrer
            // el catálogo entero. Lista vacía cuando no se pidió ninguna:
            // el campo existe siempre para que el otro lado pueda EXIGIRLO
            // cuando sí pidió (ver `reconcile._remote_partition_skus`), en
            // vez de interpretar su ausencia como "no hay SKUs".
            'partition_skus' => $wanted === []
                ? []
                : $this->contentDigest->skusOfPartitions($rows, $wanted),
        ]);
    }

    /**
     * Valida y normaliza la lista de particiones que el CLIENTE manda.
     *
     * Es entrada de red, así que se rechaza con `InputException` (400, sin
     * traza) y no con una excepción cualquiera, que el framework de web API
     * renderizaría como 500 — el mismo razonamiento que `Model\Cursor`
     * documenta para el cursor ilegible: un 500 es la clase de error que el
     * cliente reintenta, y esto no se arregla reintentando.
     *
     * Lo que se acepta es exactamente lo que `ContentDigest::partitionOf()`
     * emite: dos caracteres hex en minúsculas. No se normaliza mayúsculas a
     * minúsculas ni se toleran alias: si el otro lado empezara a mandar 'FF',
     * eso significa que los dos lados dejaron de calcular la partición igual,
     * y taparlo con un `strtolower()` esconde ese desacuerdo en vez de
     * mostrarlo.
     *
     * @return list<string>
     */
    private function parsePartitions(?string $partitions): array
    {
        if ($partitions === null || trim($partitions) === '') {
            return [];
        }

        $tokens = [];
        foreach (explode(',', $partitions) as $token) {
            $token = trim($token);
            if ($token === '') {
                continue;
            }
            if (preg_match('/^[0-9a-f]{2}$/', $token) !== 1) {
                throw new InputException(__(
                    'partición inválida: se esperaban dos caracteres hex en '
                    . 'minúsculas ("00".."ff"), tal como los emite el digest de '
                    . 'contenido de este módulo'
                ));
            }
            // Repetir una partición no es un error del cliente, pero servirla
            // dos veces sí sería un payload con la misma lista dos veces.
            $tokens[$token] = true;
        }

        if (count($tokens) > self::MAX_PARTITIONS) {
            throw new InputException(__(
                'no se pueden pedir los SKUs de más de %1 particiones por '
                . 'llamada (se recibieron %2); para más que eso, la pasada '
                . 'completa es más barata',
                self::MAX_PARTITIONS,
                count($tokens)
            ));
        }

        $keys = array_keys($tokens);
        // Las claves de un array PHP con '10' se volvieron int: se devuelven
        // como string, que es la forma que el resto del sistema usa.
        return array_map(static fn ($token): string => (string) $token, $keys);
    }
}
