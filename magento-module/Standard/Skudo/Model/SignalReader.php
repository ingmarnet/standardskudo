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
 * row_id/entity_id. EntityKeyResolver, ya usada por ProductReader (Task 8),
 * es la única que responde esa pregunta de esquema. Tampoco acota por
 * versión activa: no agrega ni un `created_in` ni un `updated_in` propios
 * (ver `Model\VersioningSchema`).
 *
 * Fix de revisión (ronda 1), dos hallazgos verificados contra la instancia
 * de referencia (no teóricos):
 *   - `margin` se leía siempre en scope global. `catalog/price/scope` está
 *     en Website ahí, y la mayoría del catálogo tiene overrides de precio
 *     por store view (165.610 filas con store_id distinto de 0). Ahora
 *     attachMargin() resuelve cost/price por scope: el override de la
 *     store view pedida SI SU FILA EXISTE (aunque el valor sea vacío — la
 *     presencia manda, no si el valor es truthy), si no el global.
 *   - `salable_qty` se leía siempre de `inventory_stock_1`, pero en la
 *     instancia de referencia el Stock 1 (Default Stock) no está asignado
 *     a ningún sitio — los sitios reales usan los stocks 2 y 3. Ahora
 *     resolveStockId() sigue la cadena real store -> website ->
 *     inventory_stock_sales_channel -> stock_id; sin una fila de canal de
 *     venta para ese sitio, salable_qty es null, nunca un stock que no le
 *     corresponde.
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
        // Inyectada, no instanciada con `new`: es la MISMA clase que usa
        // ProductReader (Task 8) para la misma pregunta de esquema. Ver
        // Ruling 3 y la clase misma.
        private readonly EntityKeyResolver $entityKeyResolver,
        // M3: ver Model\StoreViewGuard.
        private readonly StoreViewGuard $storeViewGuard,
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
        $this->storeViewGuard->assertExists($storeId);

        $connection = $this->resource->getConnection();
        $usesMsi = self::usesMsi($this->modules);

        $rows = $this->salesRows($connection, $storeId, $days, $usesMsi);
        $this->restrictToCatalogPopulation($rows);

        $this->attachInventory($rows, $usesMsi, $storeId);
        $this->attachMargin($rows, $storeId);
        $this->attachSearchDemand($rows, $storeId, $days);

        return WebApiEnvelope::wrap(['items' => array_values($rows)]);
    }

    /**
     * Ventas agregadas por SKU en la ventana, para esta store view. Esta es
     * la consulta que decide qué SKUs entran en la respuesta: solo se
     * enriquecen con inventario/margen/demanda los que tuvieron al menos
     * una venta, no el catálogo entero — eso es también lo que acota el
     * costo de attachSearchDemand() (ver ahí).
     *
     * @return array<string, mixed[]> Filas por SKU, con salable_qty,
     *     physical_qty, margin y search_demand ya en NULL (Ruling 2: el
     *     valor por defecto de "todavía no se supo" es desconocido, no
     *     cero) para que attachInventory()/attachMargin()/
     *     attachSearchDemand() solo los toquen cuando SÍ hay dato.
     */
    private function salesRows(AdapterInterface $connection, int $storeId, int $days, bool $usesMsi): array
    {
        $select = $connection->select()
            ->from(['oi' => $this->resource->getTableName('sales_order_item')], [
                'sku' => 'oi.sku',
                'units_sold' => 'SUM(oi.qty_ordered)',
                'revenue' => 'SUM(oi.row_total_incl_tax)',
                // Ruling 2 aplicada al dinero: SUM() SALTA los NULL, así que
                // una facturación parcial sale como si fuera la total. En la
                // instancia de referencia 3.238 de los 20.202 ítems de pedido
                // de los últimos 90 días tienen `row_total_incl_tax IS NULL`
                // (16%). Se cuenta cuántos faltan para poder decir
                // "desconocido" en vez de una cifra menor con aspecto exacto.
                'revenue_missing' => 'SUM(CASE WHEN oi.row_total_incl_tax IS NULL THEN 1 ELSE 0 END)',
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
            // `revenue` es la suma SOLO si no le falta ningún importe: con
            // uno solo nulo, el total real es mayor en una cantidad
            // desconocida, y con todos nulos SUM() devuelve NULL y el
            // `(float)` lo convertía en un 0.0 creíble al lado de
            // `units_sold > 0`. Los dos casos son DESCONOCIDO, no una cifra.
            $revenue = $row['revenue'] === null || (int) $row['revenue_missing'] > 0
                ? null
                : (float) $row['revenue'];

            $rows[(string) $row['sku']] = [
                'sku' => (string) $row['sku'],
                'units_sold' => (int) $row['units_sold'],
                'revenue' => $revenue,
                'salable_qty' => null,
                'physical_qty' => null,
                'uses_msi' => $usesMsi,
                'margin' => null,
                // Ruling 2: el valor por defecto de "todavía no se supo" es
                // desconocido. attachSearchDemand() lo baja a 0 solo cuando
                // comprueba que esta store view SÍ tiene datos de búsqueda.
                'search_demand' => null,
            ];
        }

        return $rows;
    }

    /**
     * A1/A2: recorta la respuesta a la MISMA población que sirve
     * `/products`.
     *
     * El contrato, explícito porque hasta ahora no lo era: `/signals`
     * reporta señales de PRODUCTOS DEL CATÁLOGO, no de líneas de pedido.
     * Un SKU que se vendió pero que el catálogo ya no tiene activo no
     * aparece.
     *
     * Sin esto, `salesRows()` hace `FROM sales_order_item`, que no es una
     * tabla staged y no recibe el filtro de versión de Magento, así que
     * `/signals` devolvía SKUs que `/products` no devuelve nunca. Dos
     * consecuencias verificadas sobre HTTP real contra la instancia de
     * desarrollo:
     *
     *   1. El espejo quedaba con una fila de `product_signal` para un SKU
     *      del que no tiene NI UN `product_record` en ninguna store view.
     *      Cualquier priorización "por dinero" que una las dos tablas lo
     *      pierde o lo une mal, y el espejo afirma una señal comercial
     *      sobre un producto que no puede describir.
     *   2. Peor: `physical_qty` llegaba NULL para un producto que SÍ tiene
     *      fila en `cataloginventory_stock_item`. `attachInventory()` une
     *      esa tabla legacy con `catalog_product_entity` para traducir
     *      product_id a sku, y Magento pone su filtro de versión en la
     *      CONDICIÓN DEL JOIN; si el producto no está en la población
     *      activa, el join no encuentra fila y la cantidad física
     *      desaparece. En la misma respuesta `salable_qty` sí llegaba
     *      (`inventory_stock_N` no es staged y no lleva filtro), así que el
     *      payload se contradecía a sí mismo: lo vendible conocido y lo
     *      físico "no se sabe". Medido: `{"sku":"SKU-GAMMA",
     *      "salable_qty":4,"physical_qty":null}` con `product_id = 1003,
     *      qty = 5` en la tabla de stock. Un desconocido fabricado a partir
     *      de un conocido, que es el mayor riesgo que nombra el spec.
     *
     * Los dos se cierran con el recorte, y no con un parche en el join: en
     * cuanto la población es la misma, la fila de entidad SIEMPRE existe
     * para todo SKU de la respuesta, así que un `physical_qty` nulo vuelve
     * a significar lo único que debe significar — no hay fila de stock.
     *
     * Es una consulta aparte y un filtro en PHP, no un JOIN dentro de la
     * agregación de ventas, a propósito: unir `sales_order_item` con la
     * tabla de entidad antes del `GROUP BY` multiplicaría `SUM(qty_ordered)`
     * por cada fila de entidad que el join devuelva, y la única garantía de
     * que devuelve una sola es la de Magento. Un error de conteo de dinero
     * es peor que una consulta más.
     *
     * @param array<string, mixed[]> $rows
     */
    private function restrictToCatalogPopulation(array &$rows): void
    {
        if ($rows === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        // Sin where de versión propio: es exactamente el mismo FROM que usan
        // `ProductReader` y `ChecksumReader`, así que la población que
        // Magento acota es la misma para los tres. Ver Model\VersioningSchema.
        $select = $connection->select()
            ->from(['e' => $this->resource->getTableName('catalog_product_entity')], ['sku' => 'e.sku'])
            ->where('e.sku IN (?)', array_keys($rows));

        $inCatalog = [];
        foreach ($connection->fetchCol($select) as $sku) {
            $inCatalog[(string) $sku] = true;
        }

        foreach (array_keys($rows) as $sku) {
            if (!isset($inCatalog[$sku])) {
                unset($rows[$sku]);
            }
        }
    }

    /**
     * Cantidad física siempre que haya fila de stock; vendible solo si MSI
     * está activo Y su tabla de índice existe. Se devuelven aparte para que
     * el ingestor no tenga que adivinar cuál está mirando (Ruling 2): con
     * MSI, lo vendible no es lo físico — la diferencia son reservas y
     * pedidos pendientes — y priorizar por la física haría enriquecer
     * productos que en realidad no se pueden vender.
     */
    private function attachInventory(array &$rows, bool $usesMsi, int $storeId): void
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
        // forzar row_id a una tabla que nunca lo usa.
        //
        // Magento pone su filtro de versión en la CONDICIÓN de este join, y
        // por eso `restrictToCatalogPopulation()` corre ANTES: si un SKU de
        // $rows no estuviera en la población activa, este join no
        // encontraría fila y `physical_qty` saldría NULL con la fila de
        // stock existiendo (A2). Con la población ya recortada, un NULL acá
        // significa lo único que debe significar: no hay fila de stock.
        $physical = $connection->select()
            ->from(['si' => $stockItem], ['qty' => 'si.qty'])
            ->join(['e' => $entity], 'e.entity_id = si.product_id', ['sku' => 'e.sku'])
            ->where('e.sku IN (?)', array_keys($rows));

        foreach ($connection->fetchAll($physical) as $row) {
            $sku = (string) $row['sku'];
            if (isset($rows[$sku]) && $row['qty'] !== null) {
                $rows[$sku]['physical_qty'] = (float) $row['qty'];
            }
        }

        if (!$usesMsi) {
            return;
        }

        $stockId = $this->resolveStockId($storeId);
        if ($stockId === null) {
            // Ruling 2: sin canal de venta mapeado para el sitio de esta
            // store view, no hay de dónde saber qué stock la sirve. null,
            // NUNCA un stock por defecto que no le corresponde — eso es
            // precisamente el bug que resolveStockId() corrige (ver ahí:
            // el Stock 1/"Default Stock" de la instancia de referencia no
            // está asignado a ningún sitio).
            return;
        }

        $salableTable = $this->resource->getTableName('inventory_stock_' . $stockId);
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
     * Resuelve el stock de MSI que sirve a esta store view, siguiendo la
     * cadena real: store -> website -> `inventory_stock_sales_channel`
     * (type "website", code = código de ese website) -> stock_id.
     *
     * Nunca se asume el Stock por defecto (id 1): en la instancia de
     * referencia ese stock no está asignado a ningún sitio (los sitios
     * reales los sirven los stocks 2 y 3, vía sus propias filas de canal de
     * venta), así que cualquier atajo a "stock 1" devolvería una cantidad
     * vendible de un almacén del que nadie vende — exactamente el bug que
     * este método corrige.
     *
     * Devuelve null en cuanto un eslabón de la cadena no tiene fila: "no sé
     * qué stock sirve este sitio" es tan desconocido como "no hay MSI", y
     * debe viajar igual — null, nunca el Stock 1 por defecto.
     */
    private function resolveStockId(int $storeId): ?int
    {
        $connection = $this->resource->getConnection();

        $websiteId = $connection->fetchOne(
            $connection->select()
                ->from($this->resource->getTableName('store'), ['website_id'])
                ->where('store_id = ?', $storeId)
        );
        if ($websiteId === false) {
            return null;
        }

        $websiteCode = $connection->fetchOne(
            $connection->select()
                ->from($this->resource->getTableName('store_website'), ['code'])
                ->where('website_id = ?', (int) $websiteId)
        );
        if ($websiteCode === false || $websiteCode === null) {
            return null;
        }

        $stockId = $connection->fetchOne(
            $connection->select()
                ->from($this->resource->getTableName('inventory_stock_sales_channel'), ['stock_id'])
                ->where('type = ?', 'website')
                ->where('code = ?', (string) $websiteCode)
        );

        return $stockId === false ? null : (int) $stockId;
    }

    /**
     * Margen = (price - cost) / price, como fracción (0.25 = 25%), la misma
     * forma que ProductSignal.margin espera del lado Python.
     *
     * Ruling 2: si `cost` (resuelto, ver abajo) no tiene fila EAV para ese
     * SKU, o la tiene con valor vacío, margin se queda en NULL — nunca se
     * calcula como si el costo fuera 0, porque "nadie cargó el costo" y "el
     * costo es cero" son hechos comerciales distintos y colapsarlos
     * falsearía la priorización (ver Ruling 2 del brief).
     *
     * `cost` y `price` se resuelven POR SCOPE, no siempre en global: se lee
     * store_id 0 (global) Y store_id = $storeId en la misma consulta, y
     * scopedValue() se queda con el override de la store view SI SU FILA
     * EXISTE (aunque el valor sea vacío — la presencia manda, no si el
     * valor es truthy; ver ese método), si no con el global. Sin esto, con
     * `catalog/price/scope` en Website (el caso de la instancia de
     * referencia, donde la mayoría de las filas de precio tienen store_id
     * distinto de 0) el margen saldría mal para casi todo el catálogo en
     * al menos uno de los sitios.
     */
    private function attachMargin(array &$rows, int $storeId): void
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
            ->join(['v' => $decimal], "v.{$keyColumn} = e.{$keyColumn}", ['store_id' => 'v.store_id'])
            ->join(
                ['a' => $attribute],
                'a.attribute_id = v.attribute_id',
                ['code' => 'a.attribute_code', 'value' => 'v.value']
            )
            ->where('e.sku IN (?)', array_keys($rows))
            ->where('a.attribute_code IN (?)', ['cost', 'price'])
            // Global Y el scope pedido en la misma pasada: scopedValue()
            // decide después cuál gana, fila por fila (ver docblock).
            ->where('v.store_id IN (?)', array_unique([0, $storeId]));

        // bySku[sku][code][store_id] = value. Se indexa por store_id (no se
        // sobreescribe "el último visto") justamente para que el orden en
        // que la fixture/el motor de base de datos entregue las filas sea
        // irrelevante: la resolución por scope depende de qué store_ids
        // tienen fila, no de cuál llegó última.
        $bySku = [];
        foreach ($connection->fetchAll($select) as $row) {
            $bySku[(string) $row['sku']][(string) $row['code']][(int) $row['store_id']] = $row['value'];
        }

        foreach ($bySku as $sku => $byCode) {
            if (!isset($rows[$sku])) {
                continue;
            }

            $cost = $this->scopedValue($byCode['cost'] ?? [], $storeId);
            $price = $this->scopedValue($byCode['price'] ?? [], $storeId);
            // "" cuenta como ausente, no como 0: una fila EAV con valor
            // vacío nunca tuvo un costo/precio real cargado para ese scope.
            if ($cost === null || $cost === '' || $price === null || $price === '' || (float) $price <= 0.0) {
                continue;
            }

            $rows[$sku]['margin'] = round(((float) $price - (float) $cost) / (float) $price, 4);
        }
    }

    /**
     * Resuelve un valor EAV por scope: el override de la store view
     * solicitada SI SU FILA EXISTE — aunque el valor sea una cadena vacía,
     * porque la presencia de la fila es lo que importa, no si el valor es
     * truthy (la misma regla de procedencia que el lado Python aplica: un
     * override cargado-pero-vacío significa "esta store view no tiene
     * valor", no "usá el global"). Si no existe fila para $storeId, cae al
     * global (store_id 0); si tampoco hay fila global, null.
     *
     * @param array<int, string|null> $valuesByStore
     */
    private function scopedValue(array $valuesByStore, int $storeId): ?string
    {
        if (array_key_exists($storeId, $valuesByStore)) {
            return $valuesByStore[$storeId];
        }

        return $valuesByStore[0] ?? null;
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
        $searchQuery = $this->resource->getTableName('search_query');

        // Ruling 2 aplicada a la demanda: si esta store view no tiene NI UNA
        // fila de búsqueda en la ventana, no hay medición que reportar y
        // search_demand se queda en NULL para todos sus SKUs. Dato real: la
        // tabla `search_query` de esta instancia tiene DOS filas en total, las
        // dos de la store 1 — con el 0 sembrado, toda señal de BR afirmaba
        // "nadie buscó nada en Brasil", una medición hecha sin datos.
        //
        // La cuenta va SIN el `popularity > 0` de abajo a propósito: ese filtro
        // es una cota de costo, no parte de la pregunta. Una tienda cuyas
        // búsquedas nunca se repitieron sí tiene datos de búsqueda, y ahí un
        // cero es un cero real.
        $hasSearchData = (int) $connection->fetchOne(
            $connection->select()
                ->from($searchQuery, ['searches' => 'COUNT(*)'])
                ->where('store_id = ?', $storeId)
                ->where('updated_at >= DATE_SUB(NOW(), INTERVAL ? DAY)', $days)
        ) > 0;

        if (!$hasSearchData) {
            return;
        }

        // A partir de acá el cero es un hecho: la tienda tiene búsquedas y
        // ninguna menciona este SKU.
        foreach (array_keys($rows) as $sku) {
            $rows[$sku]['search_demand'] = 0;
        }

        $select = $connection->select()
            ->from($searchQuery, ['query_text', 'popularity'])
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
