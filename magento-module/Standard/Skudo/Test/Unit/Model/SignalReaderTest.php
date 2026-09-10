<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;
use Standard\Skudo\Model\ActiveVersionResolver;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\SignalReader;

/**
 * Cubre S0 Task 12: usesMsi() (Step 6 del brief) y, sobre todo, la Ruling 2
 * ("NULL significa desconocido, y debe sobrevivir el viaje por la red"). Esa
 * es la regla más fácil de romper en silencio: basta con que alguien cambie
 * un `?? null` por `?? 0.0` en una futura edición y las pruebas de tipo
 * "no lanza excepción" seguirían pasando sin detectarlo. Por eso cada
 * escenario de "dato ausente" de abajo afirma explícitamente `assertNull`
 * (nunca `assertSame(0.0, ...)` ni `assertFalsy`), y cada escenario de "dato
 * presente" afirma el valor numérico exacto, para que colapsar ausencia en
 * cero rompa una aserción concreta en cualquiera de las dos direcciones.
 *
 * Como en ProductReaderTest/DeltaReaderTest, un `SignalFakeSelect` (abajo)
 * registra la tabla de cada `select()->from()` real que arma SignalReader;
 * el doble de conexión despacha `fetchAll()`/`fetchOne()` según esa tabla
 * contra filas de fixture provistas por cada prueba. Las consultas de
 * SignalReader (ventas, stock físico, resolución de stock MSI, stock
 * vendible, costo/precio, search_query) tienen tablas primarias distintas
 * entre sí, así que un fixture vacío para una tabla es indistinguible de
 * "esta consulta no encontró fila" — que es exactamente el escenario que
 * Ruling 2 exige cubrir.
 *
 * Fix de revisión (ronda 1): dos hallazgos verificados contra la instancia
 * de referencia, ninguno teórico:
 *   - `margin` se leía SIEMPRE en scope global (store_id 0), pero
 *     `catalog/price/scope` está en Website ahí y 165.610 filas de precio
 *     tienen store_id distinto de 0 — la gran mayoría del catálogo. Ahora
 *     se resuelve por scope: override de la store view solicitada si SU
 *     FILA EXISTE (aunque el valor sea vacío — la presencia manda, no si
 *     el valor es truthy), si no el valor global.
 *   - `salable_qty` se leía siempre de `inventory_stock_1`, pero en la
 *     instancia de referencia el Stock 1 (Default Stock) no está asignado
 *     a ningún sitio: los sitios reales usan los stocks 2 y 3
 *     (`inventory_stock_sales_channel`: base -> 2, website_br -> 3). Ahora
 *     se resuelve el stock real siguiendo store -> website -> canal de
 *     venta -> stock_id, y una store view sin canal mapeado da
 *     `salable_qty: null`, no el Stock 1 por defecto (ver
 *     `resolveStockId()`).
 */
class SignalReaderTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    public function testMsiAbsentFallsBackToPhysicalQuantity(): void
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->with('Magento_InventoryApi')->willReturn(false);

        $this->assertFalse(SignalReader::usesMsi($modules));
    }

    public function testMsiPresentUsesSalableQuantity(): void
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->with('Magento_InventoryApi')->willReturn(true);

        $this->assertTrue(SignalReader::usesMsi($modules));
    }

    /**
     * Ruling 2: sin MSI, salable_qty es NULL (mundo desconocido) aunque haya
     * cantidad física real. Colapsarlo a la física, o a 0, haría que el
     * ingestor priorice por un número que no significa "se puede vender".
     */
    public function testSalableQtyStaysNullWhenMsiIsAbsentEvenWithPhysicalQtyPresent(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'cataloginventory_stock_item' => [
                    ['sku' => 'SKU1', 'qty' => '9.0000'],
                ],
            ],
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertFalse($item['uses_msi']);
        $this->assertSame(9.0, $item['physical_qty']);
        $this->assertNull($item['salable_qty'], 'salable_qty no debe inventarse a partir de la física sin MSI');
    }

    /**
     * Ruling 1 + Ruling 2: MSI puede estar "instalado" según el edition pero
     * sin sus tablas de índice presentes (o viceversa: tablas remanentes de
     * una instalación previa sin el módulo activo, como se verificó en la
     * instancia de referencia). usesMsi() dice `true`, pero si la tabla de
     * stock vendible no existe, salable_qty sigue siendo NULL: la pregunta
     * no es de edición, es de "¿existe la tabla?", verificada en runtime.
     */
    public function testSalableQtyStaysNullWhenMsiTablesAreAbsentDespiteModuleBeingInstalled(): void
    {
        $reader = $this->makeReader(
            usesMsi: true,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'cataloginventory_stock_item' => [
                    ['sku' => 'SKU1', 'qty' => '9.0000'],
                ],
                // store 1 -> website 1 ('base') -> stock 1, resuelto igual
                // que en la instancia de referencia (ver resolveStockId()).
                'store' => [['website_id' => 1]],
                'store_website' => [['code' => 'base']],
                'inventory_stock_sales_channel' => [['stock_id' => 1]],
            ],
            salableTableExists: false,
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertTrue($item['uses_msi']);
        $this->assertSame(9.0, $item['physical_qty']);
        $this->assertNull($item['salable_qty'], 'sin la tabla de índice MSI no hay de dónde leer lo vendible');
    }

    /**
     * Ruling 2: con MSI y su tabla presente, salable_qty SÍ se puebla, y se
     * mantiene aparte de physical_qty (la reserva/pedido pendiente es
     * exactamente la diferencia entre las dos).
     */
    public function testSalableQtyIsPopulatedSeparatelyFromPhysicalQtyWhenMsiTableExists(): void
    {
        $reader = $this->makeReader(
            usesMsi: true,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'cataloginventory_stock_item' => [
                    ['sku' => 'SKU1', 'qty' => '9.0000'],
                ],
                'store' => [['website_id' => 1]],
                'store_website' => [['code' => 'base']],
                'inventory_stock_sales_channel' => [['stock_id' => 1]],
                'inventory_stock_1' => [
                    ['sku' => 'SKU1', 'qty' => '4.0000'],
                ],
            ],
            salableTableExists: true,
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertSame(4.0, $item['salable_qty']);
        $this->assertSame(9.0, $item['physical_qty']);
    }

    /**
     * Ruling 2: sin fila en cataloginventory_stock_item para ese producto,
     * physical_qty es NULL, no 0 — "no hay registro de stock" y "hay stock
     * en cero" son hechos comerciales distintos.
     */
    public function testPhysicalQtyStaysNullWhenThereIsNoStockItemRow(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'cataloginventory_stock_item' => [],
            ],
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertNull($item['physical_qty']);
    }

    /**
     * Ruling 2, el caso central de esta tarea: el atributo `cost` ausente
     * (o vacío) deja margin en NULL. Calcularlo como 0 falsearía la
     * priorización comercial exactamente como advierte la Ruling.
     */
    public function testMarginStaysNullWhenCostAttributeIsAbsent(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                // Solo llega 'price': 'cost' nunca se cargó para este SKU.
                'catalog_product_entity' => [
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000', 'store_id' => 0],
                ],
            ],
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertNull($item['margin']);
    }

    /**
     * Ruling 2: con cost y price presentes, margin SÍ se calcula (no se
     * queda en NULL "por las dudas"). Sin este caso, la prueba anterior
     * sería indistinguible de "margin nunca se calcula".
     */
    public function testMarginIsComputedAsAFractionWhenCostAndPriceArePresent(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'catalog_product_entity' => [
                    ['sku' => 'SKU1', 'code' => 'cost', 'value' => '15.0000', 'store_id' => 0],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000', 'store_id' => 0],
                ],
            ],
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertSame(0.25, $item['margin']);
    }

    /**
     * Ruling 2: `cost` presente pero vacío (fila EAV con value = '') debe
     * tratarse igual que ausente, no como 0 — un costo de "" nunca fue
     * cargado, y str '' == 0.0 en PHP haría que un chequeo descuidado
     * (`$cost === 0.0`) lo confundiera con un costo real de cero.
     */
    public function testMarginStaysNullWhenCostValueIsEmptyString(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'catalog_product_entity' => [
                    ['sku' => 'SKU1', 'code' => 'cost', 'value' => '', 'store_id' => 0],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000', 'store_id' => 0],
                ],
            ],
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertNull($item['margin']);
    }

    /**
     * Finding 1 de revisión: `catalog/price/scope` está en Website en la
     * instancia de referencia, y 165.610 filas de precio tienen store_id
     * distinto de 0 — leer siempre store_id 0 da el precio equivocado para
     * casi todo el catálogo en al menos uno de los dos sitios. Cada store
     * view debe usar SU propio override de precio (cost queda global acá,
     * a propósito, para aislar qué está cambiando).
     */
    public function testMarginUsesTheStoreViewsOwnPriceOverrideNotTheGlobalPrice(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'catalog_product_entity' => [
                    ['sku' => 'SKU1', 'code' => 'cost', 'value' => '10.0000', 'store_id' => 0],
                    // Precio de lista global: NINGUNA de las dos store
                    // views debe terminar usando este valor, cada una
                    // tiene su propio override.
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000', 'store_id' => 0],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '16.0000', 'store_id' => 1],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '25.0000', 'store_id' => 3],
                ],
            ],
        );

        $store1 = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));
        $store3 = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 3)));

        // (16 - 10) / 16 = 0.375, no (20 - 10) / 20 = 0.5
        $this->assertSame(0.375, $store1['margin'], 'store view 1 debe usar SU override de precio (16), no el global (20)');
        // (25 - 10) / 25 = 0.6, distinto del de la store view 1
        $this->assertSame(0.6, $store3['margin'], 'store view 3 debe usar SU PROPIO override (25), no el de la store view 1');
    }

    /**
     * Finding 1 de revisión, la regla de procedencia explícita: si la fila
     * de override de la store view EXISTE pero su valor es vacío, eso es
     * "esta store view no tiene precio cargado" — no debe caer de nuevo al
     * valor global. Es la misma regla de presencia-no-verdad que ya se
     * aplica a `cost` vacío, ahora aplicada a la resolución por scope.
     */
    public function testMarginStoreOverrideThatIsEmptyDoesNotFallBackToGlobalPrice(): void
    {
        $reader = $this->makeReader(
            usesMsi: false,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'catalog_product_entity' => [
                    ['sku' => 'SKU1', 'code' => 'cost', 'value' => '10.0000', 'store_id' => 0],
                    // La fila de la store view EXISTE, con valor vacío, y
                    // se lista ANTES que la global a propósito: una
                    // implementación que solo mire "el último valor visto
                    // para ese código" (en vez de resolver por scope de
                    // verdad) terminaría usando el global (20) por orden de
                    // llegada, no por regla. Con resolución real por scope,
                    // el orden de las filas no debe importar.
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '', 'store_id' => 1],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000', 'store_id' => 0],
                ],
            ],
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 1)));

        $this->assertNull(
            $item['margin'],
            'un override vacío no debe caer al precio global: la store view no tiene precio, punto'
        );
    }

    /**
     * Finding 2 de revisión, el caso positivo: la store view 3 (br) mapea,
     * vía su website, al stock 3 (verificado contra la instancia de
     * referencia: website_br -> stock 3). El fixture también incluye una
     * fila señuelo en `inventory_stock_1` con un valor distinto: si la
     * resolución de stock se rompiera y volviera a asumir el Stock 1, esta
     * prueba fallaría con un valor concreto (999), no solo con null.
     */
    public function testSalableQtyIsReadFromTheStockResolvedFromTheStoreViewsWebsite(): void
    {
        $reader = $this->makeReader(
            usesMsi: true,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'cataloginventory_stock_item' => [
                    ['sku' => 'SKU1', 'qty' => '9.0000'],
                ],
                'store' => [['website_id' => 2]],
                'store_website' => [['code' => 'website_br']],
                'inventory_stock_sales_channel' => [['stock_id' => 3]],
                'inventory_stock_3' => [
                    ['sku' => 'SKU1', 'qty' => '7.0000'],
                ],
                // Señuelo: si el código volviera a asumir el Stock 1, la
                // prueba vería 999.0 en vez de fallar silenciosamente.
                'inventory_stock_1' => [
                    ['sku' => 'SKU1', 'qty' => '999.0000'],
                ],
            ],
            salableTableExists: true,
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 3)));

        $this->assertSame(7.0, $item['salable_qty'], 'debía leer inventory_stock_3, no el Stock 1 por defecto');
    }

    /**
     * Finding 2 de revisión, el caso negativo: un sitio sin fila en
     * `inventory_stock_sales_channel` (el Stock 1/"Default Stock" de la
     * instancia de referencia no está asignado a ningún sitio) no tiene de
     * dónde saber qué stock lo sirve. salable_qty debe ser NULL — nunca el
     * Stock 1 por defecto, que es precisamente el bug que este fix corrige.
     */
    public function testSalableQtyIsNullWhenTheWebsiteHasNoSalesChannelMapping(): void
    {
        $reader = $this->makeReader(
            usesMsi: true,
            fixtures: [
                'sales_order_item' => [
                    ['sku' => 'SKU1', 'units_sold' => '5', 'revenue' => '100.0000'],
                ],
                'cataloginventory_stock_item' => [
                    ['sku' => 'SKU1', 'qty' => '9.0000'],
                ],
                'store' => [['website_id' => 0]],
                'store_website' => [['code' => 'admin']],
                // Sin fila para 'admin': ningún stock lo sirve.
                'inventory_stock_sales_channel' => [],
                // Señuelo: sin resolución real de stock, un código que
                // siguiera asumiendo el Stock 1 encontraría esta fila y
                // devolvería 42.0 en vez de null.
                'inventory_stock_1' => [
                    ['sku' => 'SKU1', 'qty' => '42.0000'],
                ],
            ],
            salableTableExists: true,
        );

        $item = $this->onlyItem($this->payloadOf($reader->getSignals(storeId: 0)));

        $this->assertNull($item['salable_qty']);
        $this->assertSame(9.0, $item['physical_qty'], 'physical_qty no depende de la resolución de stock MSI');
    }

    /** @param mixed[] $result */
    private function onlyItem(array $result): array
    {
        $this->assertCount(1, $result['items']);
        return $result['items'][0];
    }

    /**
     * @param array<string, mixed[]> $fixtures Filas de fetchAll() por tabla
     *     primaria (la de `from()`), ya en la forma final que SignalReader
     *     espera leer — igual que en DeltaReaderTest/ProductReaderTest, este
     *     doble no ejecuta los joins de verdad, así que la fixture ya viene
     *     "unida".
     */
    private function makeReader(bool $usesMsi, array $fixtures, bool $salableTableExists = true): SignalReader
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(
            static fn (): SignalFakeSelect => new SignalFakeSelect()
        );
        $connection->method('fetchAll')->willReturnCallback(
            static fn (SignalFakeSelect $select): array => $fixtures[$select->table] ?? []
        );
        // resolveStockId() encadena tres fetchOne(): store -> store_website
        // -> inventory_stock_sales_channel, cada uno pidiendo una sola
        // columna. Como con fetchAll, se despacha por tabla primaria contra
        // la fixture de esa prueba; sin fila (o tabla ausente), false — lo
        // mismo que devolvería Zend_Db_Adapter cuando la consulta no
        // encuentra nada, y es lo que hace que la cadena corte a null.
        $connection->method('fetchOne')->willReturnCallback(
            static function (SignalFakeSelect $select) use ($fixtures) {
                $tableRows = $fixtures[$select->table] ?? [];
                if ($tableRows === []) {
                    return false;
                }
                $column = $select->columns[0] ?? null;
                $row = $tableRows[0];
                return $column !== null && array_key_exists($column, $row) ? $row[$column] : false;
            }
        );
        // El nombre de tabla ya no es siempre 'inventory_stock_1': ahora
        // depende del stock resuelto (ver resolveStockId()).
        $connection->method('isTableExists')->willReturnCallback(
            static fn (string $table): bool => str_starts_with($table, 'inventory_stock_') && $salableTableExists
        );
        // Sin versionado en estos fixtures: EntityKeyResolver resuelve a
        // entity_id y ActiveVersionResolver no agrega ningún where. Esa
        // rama ya está cubierta a fondo por ProductReaderTest/DeltaReaderTest;
        // acá no es el objeto de prueba.
        $connection->method('tableColumnExists')->willReturn(false);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->with('Magento_InventoryApi')->willReturn($usesMsi);

        return new SignalReader(
            $resource,
            $modules,
            new EntityKeyResolver($resource),
            new ActiveVersionResolver($resource)
        );
    }
}

/**
 * Doble de prueba de `Magento\Framework\DB\Select` para SignalReaderTest:
 * registra solo la tabla primaria (`from()`); ver la clase homónima en
 * ProductReaderTest para el razonamiento completo de por qué se extiende la
 * clase real en vez de solo imitar su interfaz.
 */
final class SignalFakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var list<string> Columnas planas pedidas por from(), para fetchOne(). */
    public array $columns = [];

    public function __construct()
    {
    }

    /**
     * @param mixed $tables
     * @param string|array<int|string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        $this->table = (string) array_values((array) $tables)[0];
        $this->columns = is_array($columns) ? array_values($columns) : [(string) $columns];
        return $this;
    }

    /**
     * @param mixed $tables
     * @param string|array<int|string, string> $columns
     * @param mixed $schema
     */
    public function join($tables, $cond, $columns = '*', $schema = null): self
    {
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        return $this;
    }

    public function group($spec): self
    {
        return $this;
    }

    public function order($spec): self
    {
        return $this;
    }

    public function limit($count = null, $offset = null): self
    {
        return $this;
    }
}
