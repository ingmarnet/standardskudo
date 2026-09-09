<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use Standard\Skudo\Api\SignalReaderInterface;

/**
 * S0 Task 12: señales comerciales por (SKU, store view) — ventas, stock
 * físico vs. vendible, margen y una demanda de búsqueda aproximada. Esta
 * clase NO puntúa ni prioriza nada: solo reporta números. El ordenamiento
 * por dinero vive del lado Python (skudo.mirror.signals), que es quien
 * decide qué hacer con `null` (desconocido) frente a un valor real.
 *
 * Ruling 1 (S0): MSI (Magento_InventoryApi) viene con Open Source, así que
 * "¿hay MSI?" NUNCA se decide a partir del edition string — se decide
 * comprobando el módulo Y, por separado, si sus tablas de índice existen
 * (ver attachInventory(): la instancia de referencia tiene las tablas
 * `inventory_stock_*` con datos pero el módulo mismo ausente de
 * setup_module, remanente de una instalación MSI anterior — exactamente el
 * caso que "asumir por edición" pasaría por alto).
 *
 * Ruling 3 (S0): esta clase no reimplementa la detección de
 * row_id/entity_id ni la ventana de versión activa de Magento_Staging.
 * EntityKeyResolver y ActiveVersionResolver, ya usadas por ProductReader
 * (Task 8) y DeltaReader (Task 10), son las únicas que responden esas dos
 * preguntas de esquema.
 */
class SignalReader implements SignalReaderInterface
{
    /**
     * Tope de filas de `search_query` comparadas en attachSearchDemand().
     * Ver ese método para el porqué del acotamiento.
     */
    private const MAX_SEARCH_QUERIES = 2000;

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly ModuleListInterface $modules,
        // Inyectadas, no instanciadas con `new`: son las MISMAS clases que
        // usan ProductReader (Task 8) y DeltaReader (Task 10) para las
        // mismas dos preguntas de esquema. Ver Ruling 3 y las clases mismas.
        private readonly EntityKeyResolver $entityKeyResolver,
        private readonly ActiveVersionResolver $activeVersionResolver,
    ) {
    }

    /**
     * Único lugar que decide si el mundo de inventario es "con MSI" o "sin
     * MSI": comprobando si el módulo está instalado, nunca a partir del
     * edition. Estático y separado de getSignals() para que
     * SignalReaderTest pueda probarlo sin construir toda la clase (Step 6
     * del brief de Task 12).
     */
    public static function usesMsi(ModuleListInterface $modules): bool
    {
        return $modules->has('Magento_InventoryApi');
    }

    public function getSignals(int $storeId, int $days = 90): array
    {
        $connection = $this->resource->getConnection();
        $usesMsi = self::usesMsi($this->modules);

        $rows = $this->salesRows($connection, $storeId, $days, $usesMsi);

        $this->attachInventory($rows, $usesMsi);
        $this->attachMargin($rows);
        $this->attachSearchDemand($rows, $storeId, $days);

        return ['items' => array_values($rows)];
    }

    /**
     * Ventas agregadas por SKU en la ventana, para esta store view. Esta es
     * la consulta que decide qué SKUs entran en la respuesta: solo se
     * enriquecen con inventario/margen/demanda los que tuvieron al menos
     * una venta, no el catálogo entero — eso es también lo que acota el
     * costo de attachSearchDemand() (ver ahí).
     *
     * @return array<string, mixed[]> Filas por SKU, con salable_qty,
     *     physical_qty y margin ya en NULL (Ruling 2: el valor por defecto
     *     de "todavía no se supo" es desconocido, no cero) para que
     *     attachInventory()/attachMargin() solo los toquen cuando SÍ hay
     *     dato.
     */
    private function salesRows(AdapterInterface $connection, int $storeId, int $days, bool $usesMsi): array
    {
        $select = $connection->select()
            ->from(['oi' => $this->resource->getTableName('sales_order_item')], [
                'sku' => 'oi.sku',
                'units_sold' => 'SUM(oi.qty_ordered)',
                'revenue' => 'SUM(oi.row_total_incl_tax)',
            ])
            ->join(
                ['o' => $this->resource->getTableName('sales_order')],
                'o.entity_id = oi.order_id',
                []
            )
            ->where('o.store_id = ?', $storeId)
            ->where('o.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)', $days)
            ->group('oi.sku');

        $rows = [];
        foreach ($connection->fetchAll($select) as $row) {
            $rows[(string) $row['sku']] = [
                'sku' => (string) $row['sku'],
                'units_sold' => (int) $row['units_sold'],
                'revenue' => (float) $row['revenue'],
                'salable_qty' => null,
                'physical_qty' => null,
                'uses_msi' => $usesMsi,
                'margin' => null,
                'search_demand' => 0,
            ];
        }

        return $rows;
    }

    /**
     * Cantidad física siempre que haya fila de stock; vendible solo si MSI
     * está activo Y su tabla de índice existe. Se devuelven aparte para que
     * el ingestor no tenga que adivinar cuál está mirando (Ruling 2): con
     * MSI, lo vendible no es lo físico — la diferencia son reservas y
     * pedidos pendientes — y priorizar por la física haría enriquecer
     * productos que en realidad no se pueden vender.
     */
    private function attachInventory(array &$rows, bool $usesMsi): void
    {
        if ($rows === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        $entity = $this->resource->getTableName('catalog_product_entity');
        $stockItem = $this->resource->getTableName('cataloginventory_stock_item');

        // cataloginventory_stock_item.product_id SIEMPRE referencia
        // entity_id, nunca row_id: es una tabla legacy, no versionada por
        // Magento_Staging (verificado en la instancia de referencia: unir
        // por entity_id recupera las 228.881 filas del catálogo completo;
        // por row_id, una intersección parcial que coincide por casualidad
        // de rango de IDs). Por eso acá NO se usa EntityKeyResolver — sería
        // la reimplementación que la Ruling 3 previene, aplicada al revés:
        // forzar row_id a una tabla que nunca lo usa. El filtro de versión
        // activa SÍ aplica al lado `e` del join (para no traer el mismo
        // stock físico una vez por cada versión programada de un producto).
        $physical = $connection->select()
            ->from(['si' => $stockItem], ['qty' => 'si.qty'])
            ->join(['e' => $entity], 'e.entity_id = si.product_id', ['sku' => 'e.sku'])
            ->where('e.sku IN (?)', array_keys($rows));
        $this->activeVersionResolver->applyToSelect($physical, 'e.');

        foreach ($connection->fetchAll($physical) as $row) {
            $sku = (string) $row['sku'];
            if (isset($rows[$sku]) && $row['qty'] !== null) {
                $rows[$sku]['physical_qty'] = (float) $row['qty'];
            }
        }

        if (!$usesMsi) {
            return;
        }

        // Asume el Stock por defecto (id 1): el indexador de MSI nombra su
        // tabla de vendible por stock (`inventory_stock_<id>`), y resolver
        // el stock real de cada sitio/canal de venta de $storeId excede el
        // alcance de esta tarea (Task 12 solo pide "vendible vs. física",
        // no multi-stock). Con un solo Stock configurado (el caso común),
        // esta suposición es correcta; documentarla acá es preferible a
        // que quede implícita.
        $salableTable = $this->resource->getTableName('inventory_stock_1');
        if (!$connection->isTableExists($salableTable)) {
            // Ruling 1: MSI puede figurar "instalado" y aun así no tener su
            // tabla de índice (o al revés, como en la instancia de
            // referencia). Sin la tabla, salable_qty se queda en null: no
            // hay de dónde leerlo, y null es la respuesta correcta, no 0.
            return;
        }

        $salable = $connection->select()
            ->from(['s' => $salableTable], ['sku' => 's.sku', 'qty' => 's.quantity'])
            ->where('s.sku IN (?)', array_keys($rows));

        foreach ($connection->fetchAll($salable) as $row) {
            $sku = (string) $row['sku'];
            if (isset($rows[$sku]) && $row['qty'] !== null) {
                $rows[$sku]['salable_qty'] = (float) $row['qty'];
            }
        }
    }

    /**
     * Margen = (price - cost) / price, como fracción (0.25 = 25%), la misma
     * forma que ProductSignal.margin espera del lado Python.
     *
     * Ruling 2: si `cost` no tiene fila EAV para ese SKU, o la tiene con
     * valor vacío, margin se queda en NULL — nunca se calcula como si el
     * costo fuera 0, porque "nadie cargó el costo" y "el costo es cero" son
     * hechos comerciales distintos y colapsarlos falsearía la
     * priorización (ver Ruling 2 del brief).
     *
     * Se lee en scope global (store_id = 0), que es donde `cost` vive
     * siempre en el catálogo de referencia (is_global = 2, Global) y donde
     * `price` vive salvo que "Catalog Price Scope" esté en Website — en ese
     * caso el valor por sitio se guarda bajo el store_id de la store view
     * por defecto de ese website, no bajo 0. Esta implementación no resuelve
     * esa variante (leería el precio de lista global en vez del de sitio);
     * documentado acá como simplificación conocida, no como parte de la
     * Ruling 2 (que es sobre null-vs-cero, no sobre scope de atributo).
     */
    private function attachMargin(array &$rows): void
    {
        if ($rows === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        $keyColumn = $this->entityKeyResolver->resolve();
        $entity = $this->resource->getTableName('catalog_product_entity');
        $decimal = $this->resource->getTableName('catalog_product_entity_decimal');
        $attribute = $this->resource->getTableName('eav_attribute');

        $select = $connection->select()
            ->from(['e' => $entity], ['sku' => 'e.sku'])
            ->join(['v' => $decimal], "v.{$keyColumn} = e.{$keyColumn}", [])
            ->join(
                ['a' => $attribute],
                'a.attribute_id = v.attribute_id',
                ['code' => 'a.attribute_code', 'value' => 'v.value']
            )
            ->where('e.sku IN (?)', array_keys($rows))
            ->where('a.attribute_code IN (?)', ['cost', 'price'])
            ->where('v.store_id = ?', 0);
        $this->activeVersionResolver->applyToSelect($select, 'e.');

        $bySku = [];
        foreach ($connection->fetchAll($select) as $row) {
            $bySku[(string) $row['sku']][(string) $row['code']] = $row['value'];
        }

        foreach ($bySku as $sku => $values) {
            if (!isset($rows[$sku])) {
                continue;
            }

            $cost = $values['cost'] ?? null;
            $price = $values['price'] ?? null;
            // "" cuenta como ausente, no como 0: una fila EAV con valor
            // vacío nunca tuvo un costo real cargado.
            if ($cost === null || $cost === '' || $price === null || $price === '' || (float) $price <= 0.0) {
                continue;
            }

            $rows[$sku]['margin'] = round(((float) $price - (float) $cost) / (float) $price, 4);
        }
    }

    /**
     * ATRIBUCIÓN APROXIMADA, no demanda medida. Magento core no vincula una
     * búsqueda de `search_query` con los productos que esa búsqueda mostró
     * o que el usuario terminó viendo: eso requeriría un índice de clics que
     * el core no tiene. La única señal disponible sin eso es si el SKU
     * aparece como subcadena del texto buscado, que:
     *   - PIERDE demanda real (alguien busca por nombre, marca o sinónimo,
     *     nunca por el SKU exacto);
     *   - ATRIBUYE MAL ocasionalmente (un SKU corto puede calzar dentro de
     *     otro término por coincidencia, sin relación real).
     * Se implementa así porque es lo que esta tarea pide, no porque sea una
     * medición confiable: nadie del lado Python (ni ningún consumidor
     * futuro) debería leer `search_demand` como "esto es lo que la gente
     * buscó", sino como una señal débil y direccional.
     *
     * Costo acotado en dos frentes, porque el catálogo piloto tiene 228.881
     * productos y una comparación sin acotar (cada query × cada SKU del
     * catálogo) no es aceptable a esa escala:
     *   1. Los SKUs contra los que se compara son solo los de $rows (los
     *      que tuvieron venta en la ventana), nunca el catálogo completo.
     *   2. El conjunto de search_query se acota a `popularity > 0` dentro
     *      de la ventana (descarta búsquedas que nunca se repitieron, la
     *      mayoría en cualquier tienda real) y a MAX_SEARCH_QUERIES filas,
     *      ordenadas por popularidad descendente, así que si el tope se
     *      alcanza son las búsquedas MENOS repetidas las que quedan afuera,
     *      no un corte arbitrario.
     */
    private function attachSearchDemand(array &$rows, int $storeId, int $days): void
    {
        if ($rows === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        $select = $connection->select()
            ->from($this->resource->getTableName('search_query'), ['query_text', 'popularity'])
            ->where('store_id = ?', $storeId)
            ->where('updated_at >= DATE_SUB(NOW(), INTERVAL ? DAY)', $days)
            ->where('popularity > 0')
            ->order('popularity DESC')
            ->limit(self::MAX_SEARCH_QUERIES);

        foreach ($connection->fetchAll($select) as $row) {
            $text = strtolower((string) $row['query_text']);
            if ($text === '') {
                continue;
            }
            foreach ($rows as $sku => $_) {
                if (str_contains($text, strtolower($sku))) {
                    $rows[$sku]['search_demand'] += (int) $row['popularity'];
                }
            }
        }
    }
}
