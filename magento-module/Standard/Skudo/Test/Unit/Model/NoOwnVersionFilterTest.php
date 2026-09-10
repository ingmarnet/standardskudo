<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\CategoryReader;
use Standard\Skudo\Model\ChecksumReader;
use Standard\Skudo\Model\Cursor;
use Standard\Skudo\Model\DeltaReader;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\EnvironmentProbe;
use Standard\Skudo\Model\ProductReader;
use Standard\Skudo\Model\SignalReader;
use Standard\Skudo\Model\VersioningSchema;

/**
 * La prueba que impide que el filtro de versión propio vuelva (C2).
 *
 * El módulo tenía su propia ventana de "versión activa" —`created_in <=
 * UNIX_TIMESTAMP() AND updated_in > UNIX_TIMESTAMP()`— encima de la que
 * Magento ya inyecta en todo `Select` del framework sobre una tabla staged.
 * Los dos filtros anclan en cosas distintas (el id de la versión APLICADA
 * uno, el reloj el otro), así que su conjunción es estrictamente más
 * estrecha que cualquiera de los dos y el módulo perdía filas que la tienda
 * sí sirve. Ver `Model\VersioningSchema` para la medición.
 *
 * Por qué esta prueba está escrita así, y no como las que reemplaza: las
 * pruebas viejas afirmaban la PRESENCIA de esos dos `where()` con un
 * `FakeSelect` propio, que por construcción no sabe nada del renderer de
 * Magento. Es exactamente el mecanismo por el que el defecto se escondió:
 * un doble que no modela el filtro del framework nunca puede mostrar la
 * conjunción de los dos. Esta prueba no afirma nada sobre lo que Magento
 * hace —no puede, sin una instancia— sino sólo lo que el MÓDULO emite, que
 * es lo único que el módulo controla y lo único que hay que mantener en
 * cero. La verificación de que el resultado coincide con la población que
 * Magento considera activa se hace contra una instancia real:
 * `Test/Integration/PopulationMatchesMagentoTest`.
 *
 * Las seis clases se construyen con el esquema versionado PRESENTE
 * (`tableColumnExists` => true), que es la única configuración en la que un
 * filtro propio podría volver a colarse.
 */
class NoOwnVersionFilterTest extends TestCase
{
    /**
     * `created_in > ?` de `DeltaReader::activatedVersions()` es el ÚNICO
     * predicado del módulo que puede nombrar una columna de versión: no es
     * una ventana de "activa", es "lo que se activó desde la última
     * lectura". Cualquier otro es una regresión de C2.
     */
    private const ALLOWED_VERSION_PREDICATES = ['created_in > ?'];

    /**
     * @return array<string, array{0: string}>
     */
    public static function readerProvider(): array
    {
        return [
            'ProductReader::getPage' => ['product_page'],
            'ProductReader::getBySku' => ['product_by_sku'],
            'ChecksumReader::getChecksums' => ['checksums'],
            'CategoryReader::getPage' => ['categories'],
            'SignalReader::getSignals' => ['signals'],
            'EnvironmentProbe::getProfile' => ['environment'],
            'DeltaReader::getChanges' => ['deltas'],
        ];
    }

    /**
     * @dataProvider readerProvider
     */
    public function testNoReaderEmitsAVersionWindowOfItsOwn(string $key): void
    {
        $selects = [];
        $this->invoke($key, $selects);

        $this->assertNotSame(
            [],
            $selects,
            "{$key} no ejecutó ninguna consulta: la prueba sería vacua y no probaría nada"
        );

        $offending = [];
        foreach ($selects as $select) {
            foreach ($select->wheres as $where) {
                $cond = (string) $where['cond'];
                if (!str_contains($cond, 'created_in') && !str_contains($cond, 'updated_in')) {
                    continue;
                }
                if (in_array($cond, self::ALLOWED_VERSION_PREDICATES, true)) {
                    continue;
                }
                $offending[] = $cond;
            }
        }

        $this->assertSame(
            [],
            $offending,
            "{$key} emite un predicado propio sobre created_in/updated_in. Magento ya acota "
            . 'todo Select del framework sobre una tabla staged a la versión aplicada; un '
            . 'segundo filtro anclado en otra cosa (el reloj) hace que el módulo pierda filas '
            . 'que la tienda sí sirve, y que /checksums las pierda igual, así que reconcile() '
            . 'reporta "sin deriva" sobre el conjunto equivocado. Ver Model\\VersioningSchema.'
        );
    }

    /**
     * Control positivo: la prueba de arriba tiene que ser capaz de FALLAR.
     * Si el recolector de `where()` no funcionara (un doble que no registra,
     * un reader que no llega al `where`), la aserción de "cero predicados"
     * pasaría trivialmente para siempre. Acá se mete a mano el predicado
     * exacto que C2 introducía y se comprueba que el mismo criterio lo
     * detecta.
     */
    public function testTheDetectionItselfCatchesTheFilterThatWasRemoved(): void
    {
        $select = new RecordingSelect();
        $select->where('e.created_in <= UNIX_TIMESTAMP()')
            ->where('e.updated_in > UNIX_TIMESTAMP()');

        $offending = [];
        foreach ($select->wheres as $where) {
            $cond = (string) $where['cond'];
            if (str_contains($cond, 'created_in') || str_contains($cond, 'updated_in')) {
                if (!in_array($cond, self::ALLOWED_VERSION_PREDICATES, true)) {
                    $offending[] = $cond;
                }
            }
        }

        $this->assertSame(
            ['e.created_in <= UNIX_TIMESTAMP()', 'e.updated_in > UNIX_TIMESTAMP()'],
            $offending
        );
    }

    /**
     * El predicado permitido no es "cualquier cosa con created_in": la
     * consulta de activaciones de `DeltaReader` debe emitir exactamente uno,
     * y ninguna ventana. Si alguien "arreglara" C2 dejando el filtro sólo
     * ahí, esta prueba lo señala.
     */
    public function testTheDeltaActivationQueryEmitsOnlyTheSinceTimestampPredicate(): void
    {
        $selects = [];
        $this->invoke('deltas', $selects);

        $entitySelects = array_values(array_filter(
            $selects,
            static fn (RecordingSelect $s): bool => $s->table === 'catalog_product_entity'
        ));

        $this->assertCount(
            1,
            $entitySelects,
            'con sinceTimestamp y esquema versionado debe haber exactamente una consulta '
            . 'de activaciones sobre catalog_product_entity'
        );

        $this->assertSame(
            ['created_in > ?'],
            array_column($entitySelects[0]->wheres, 'cond'),
            'la consulta de activaciones acota por sinceTimestamp y NADA más: la ventana de '
            . '"versión activa" la pone Magento sobre este mismo FROM'
        );
    }

    /**
     * @param list<RecordingSelect> $selects
     */
    private function invoke(string $key, array &$selects): void
    {
        $resource = $this->resource($selects);

        match ($key) {
            'product_page' => $this->productReader($resource)->getPage(storeId: 1, limit: 10),
            'product_by_sku' => $this->productReader($resource)->getBySku(1, ['SKU-A']),
            'checksums' => (new ChecksumReader($resource))->getChecksums(1),
            'categories' => $this->categoryReader($resource)->getPage(10),
            'signals' => $this->signalReader($resource)->getSignals(1, 90),
            'environment' => $this->environmentProbe($resource)->getProfile(),
            'deltas' => (new DeltaReader($resource, new VersioningSchema($resource)))
                ->getChanges(0, 10, 1_700_000_000),
        };
    }

    private function productReader(ResourceConnection $resource): ProductReader
    {
        return new ProductReader($resource, new Cursor(), new EntityKeyResolver($resource));
    }

    private function categoryReader(ResourceConnection $resource): CategoryReader
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStores')->willReturn([]);

        return new CategoryReader($resource, new Cursor(), new EntityKeyResolver($resource), $storeManager);
    }

    private function signalReader(ResourceConnection $resource): SignalReader
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(true);

        return new SignalReader($resource, $modules, new EntityKeyResolver($resource));
    }

    private function environmentProbe(ResourceConnection $resource): EnvironmentProbe
    {
        $metadata = $this->createMock(\Magento\Framework\App\ProductMetadataInterface::class);
        $metadata->method('getEdition')->willReturn('Enterprise');
        $metadata->method('getVersion')->willReturn('2.4.8-p3');

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(true);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getWebsites')->willReturn([]);
        $storeManager->method('getGroups')->willReturn([]);
        $storeManager->method('getStores')->willReturn([]);

        return new EnvironmentProbe($metadata, $modules, $resource, $storeManager, new EntityKeyResolver($resource));
    }

    /**
     * Conexión que sólo devuelve filas suficientes para que cada lector
     * llegue a armar todas sus consultas. `tableColumnExists` siempre true:
     * esquema versionado presente, la configuración en la que un filtro
     * propio podría volver.
     *
     * @param list<RecordingSelect> $selects
     */
    private function resource(array &$selects): ResourceConnection
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(
            static function () use (&$selects): RecordingSelect {
                $select = new RecordingSelect();
                $selects[] = $select;
                return $select;
            }
        );
        $connection->method('tableColumnExists')->willReturn(true);
        $connection->method('isTableExists')->willReturn(true);
        $connection->method('fetchOne')->willReturn('4');
        $connection->method('fetchCol')->willReturn([]);
        $connection->method('fetchAll')->willReturnCallback(
            static function (RecordingSelect $select): array {
                // `catalog_product_entity` devuelve una fila para que la
                // proyección siga (y arme las consultas EAV/website/
                // categoría); `sales_order_item`, una fila de venta para que
                // SignalReader llegue a inventario/margen/demanda. El resto
                // vacío: lo que se mide son los WHERE, no los datos.
                return match ($select->table) {
                    'catalog_product_entity' => [[
                        'key' => 1, 'sku' => 'SKU-A', 'attribute_set_id' => 4,
                        'type_id' => 'simple', 'updated_at' => '2026-01-01 00:00:00',
                        'created_in' => 1, 'updated_in' => 2_147_483_647,
                        // Las tres últimas son para la consulta de margen de
                        // SignalReader, que también hace FROM de esta tabla.
                        'code' => 'price', 'store_id' => 0, 'value' => '10.0000',
                    ]],
                    'sales_order_item' => [[
                        'sku' => 'SKU-A', 'units_sold' => '1', 'revenue' => '10.0000',
                        'revenue_missing' => '0',
                    ]],
                    'eav_attribute' => [['attribute_id' => 1, 'backend_type' => 'varchar']],
                    default => [],
                };
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return $resource;
    }
}

/**
 * Doble de `Select` que registra tabla y `where()`. A diferencia de los
 * `FakeSelect` de las otras pruebas, NO evalúa condiciones contra filas:
 * acá lo que se mide es qué predicados emite el módulo, no qué devuelven.
 */
final class RecordingSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var list<array{cond: string, value: mixed}> */
    public array $wheres = [];

    public function __construct()
    {
    }

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        $this->table = (string) array_values((array) $tables)[0];
        return $this;
    }

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function join($tables, $cond, $columns = '*', $schema = null): self
    {
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[] = ['cond' => $cond, 'value' => $value];
        return $this;
    }

    public function order($spec): self
    {
        return $this;
    }

    public function group($spec): self
    {
        return $this;
    }

    public function limit($count = null, $offset = null): self
    {
        return $this;
    }
}
