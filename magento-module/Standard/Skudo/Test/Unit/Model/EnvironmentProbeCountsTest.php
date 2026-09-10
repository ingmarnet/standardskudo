<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\EntityTypeResolver;
use Standard\Skudo\Model\EnvironmentProbe;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;

/**
 * M1: `counts` tiene que contar la MISMA población que el endpoint que sirve
 * cada cosa, o la diferencia se lee como deriva del espejo.
 *
 * Los tres desacuerdos medidos sobre HTTP real contra la instancia de
 * desarrollo:
 *   - `products` 8 contra `/checksums.product_count` 7 (era C2: los dos
 *     endpoints agregaban una ventana de versión propia y este COUNT(*) no).
 *     Se cierra quitando el filtro, no tocando el conteo:
 *     `PopulationMatchesMagentoTest` afirma la igualdad contra una instancia
 *     real, y `testTheProductCountHasNoWhereOfItsOwn` fija acá que este
 *     COUNT(*) siga sin where propio — si le agregaran uno, volvería a
 *     desacordar con /products.
 *   - `attributes` 1.240 (todos los entity types) contra los 1.066 de
 *     `catalog_product` que `/attributes` pagina.
 *   - `attribute_sets` 422 contra los 413 de `catalog_product`.
 */
class EnvironmentProbeCountsTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    private const PRODUCT_ENTITY_TYPE = 17;

    public function testAttributeAndAttributeSetCountsAreScopedToTheProductEntityType(): void
    {
        $selects = [];
        $counts = $this->payloadOf($this->probe($selects)->getProfile())['counts'];

        $this->assertSame(
            [
                'eav_attribute' => ['entity_type_id = ?' => self::PRODUCT_ENTITY_TYPE],
                'eav_attribute_set' => ['entity_type_id = ?' => self::PRODUCT_ENTITY_TYPE],
            ],
            [
                'eav_attribute' => $this->wheresFor($selects, 'eav_attribute'),
                'eav_attribute_set' => $this->wheresFor($selects, 'eav_attribute_set'),
            ],
            'los conteos de atributos y de attribute sets tienen que acotarse al entity type '
            . 'de producto, que es el único que este módulo sirve'
        );

        // El entity type se resuelve, no se asume: 17 y no el "típico" 4.
        $this->assertSame(4, count($counts));
    }

    /**
     * El conteo de productos NO lleva where propio, a propósito: Magento
     * acota este COUNT(*) igual que acota `/products` y `/checksums`, y ahí
     * está la coincidencia. Un where propio acá reintroduciría M1 desde el
     * otro lado.
     */
    public function testTheProductCountHasNoWhereOfItsOwn(): void
    {
        $selects = [];
        $this->probe($selects)->getProfile();

        $this->assertSame([], $this->wheresFor($selects, 'catalog_product_entity'));
        $this->assertSame([], $this->wheresFor($selects, 'catalog_category_entity'));
    }

    /**
     * @param list<CountingSelect> $selects
     * @return array<string, mixed>
     */
    private function wheresFor(array $selects, string $table): array
    {
        foreach ($selects as $select) {
            if ($select->table === $table && $select->columns === 'COUNT(*)') {
                return $select->wheres;
            }
        }

        $this->fail("no se ejecutó ningún COUNT(*) sobre {$table}");
    }

    /**
     * @param list<CountingSelect> $selects
     */
    private function probe(array &$selects): EnvironmentProbe
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(
            static function () use (&$selects): CountingSelect {
                $select = new CountingSelect();
                $selects[] = $select;
                return $select;
            }
        );
        $connection->method('tableColumnExists')->willReturn(false);
        $connection->method('isTableExists')->willReturn(false);
        $connection->method('fetchCol')->willReturn([]);
        $connection->method('fetchOne')->willReturnCallback(
            static fn (CountingSelect $select): string => $select->table === 'eav_entity_type'
                ? (string) self::PRODUCT_ENTITY_TYPE
                : '0'
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $metadata = $this->createMock(ProductMetadataInterface::class);
        $metadata->method('getEdition')->willReturn('Enterprise');
        $metadata->method('getVersion')->willReturn('2.4.8-p3');

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(false);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getWebsites')->willReturn([]);
        $storeManager->method('getGroups')->willReturn([]);
        $storeManager->method('getStores')->willReturn([]);

        return new EnvironmentProbe(
            $metadata,
            $modules,
            $resource,
            $storeManager,
            new EntityKeyResolver($resource),
            new EntityTypeResolver($resource)
        );
    }
}

/**
 * Doble de `Select` que recuerda tabla, columnas y `where()`: los tres son
 * lo que estas pruebas afirman sobre cada COUNT(*).
 */
final class CountingSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var string|array<string, string> */
    public $columns = '';

    /** @var array<string, mixed> */
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
        $this->columns = $columns;
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[(string) $cond] = $value;
        return $this;
    }
}
