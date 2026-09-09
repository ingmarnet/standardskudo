<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use PHPUnit\Framework\TestCase;
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
 * el doble de conexión despacha `fetchAll()` según esa tabla contra filas de
 * fixture provistas por cada prueba. Las cinco consultas de SignalReader
 * (ventas, stock físico, stock vendible MSI, costo/precio, search_query)
 * tienen tablas primarias distintas entre sí, así que un fixture vacío para
 * una tabla es indistinguible de "esta consulta no encontró fila" — que es
 * exactamente el escenario que Ruling 2 exige cubrir.
 */
class SignalReaderTest extends TestCase
{
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

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

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
            ],
            salableTableExists: false,
        );

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

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
                'inventory_stock_1' => [
                    ['sku' => 'SKU1', 'qty' => '4.0000'],
                ],
            ],
            salableTableExists: true,
        );

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

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

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

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
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000'],
                ],
            ],
        );

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

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
                    ['sku' => 'SKU1', 'code' => 'cost', 'value' => '15.0000'],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000'],
                ],
            ],
        );

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

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
                    ['sku' => 'SKU1', 'code' => 'cost', 'value' => ''],
                    ['sku' => 'SKU1', 'code' => 'price', 'value' => '20.0000'],
                ],
            ],
        );

        $item = $this->onlyItem($reader->getSignals(storeId: 1));

        $this->assertNull($item['margin']);
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
        $connection->method('isTableExists')->willReturnCallback(
            static fn (string $table): bool => $table === 'inventory_stock_1' && $salableTableExists
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
