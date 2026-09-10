<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\Cursor;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\ProductReader;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;

/**
 * `ProductReader::categoryIds()` es la única consulta del lector que ejecuta
 * un join de verdad en pruebas: el `FakeSelect::join()` de
 * `ProductReaderTest` es un no-op, así que ahí las subconsultas EAV/website/
 * categoría nunca se evalúan. Este archivo trae un doble que SÍ resuelve el
 * join contra filas de fixture.
 *
 * Historia, porque explica por qué el archivo se llama así: se creó para el
 * hallazgo L1, "categoryIds() no aplica el filtro de versión vigente", con la
 * premisa de que un sku con tres filas de versión devolvía la misma categoría
 * tres veces. Esa premisa NO se sostiene en una petición real (C2): el join a
 * `catalog_product_entity` es un FROM de tabla staged, y Magento ya lo acota a
 * la versión aplicada, así que a lo sumo hay una fila de versión por entidad.
 * Verificado sobre la instancia de desarrollo: `categoryIds()` devuelve cada
 * categoría una sola vez sin ningún filtro del módulo. Las dos pruebas que
 * afirmaban lo contrario se borraron con el filtro; queda la que fija el
 * comportamiento que sí es del módulo.
 */
class ProductReaderCategoryIdsTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    private const NOW = 2_000_000_000;

    /**
     * Las tres versiones de NGO-T2092: una expirada, la vigente, y una
     * programada a futuro. Las tres apuntan a la categoría 603.
     */
    private const VERSION_ROWS = [
        ['row_id' => 1, 'entity_id' => 77096, 'sku' => 'NGO-T2092', 'attribute_set_id' => 4,
         'type_id' => 'simple', 'updated_at' => '2026-01-01 00:00:00',
         'created_in' => self::NOW - 2000, 'updated_in' => self::NOW - 1000],
        ['row_id' => 2, 'entity_id' => 77096, 'sku' => 'NGO-T2092', 'attribute_set_id' => 4,
         'type_id' => 'simple', 'updated_at' => '2026-01-01 00:00:00',
         'created_in' => self::NOW - 1000, 'updated_in' => self::NOW + 1000],
        ['row_id' => 3, 'entity_id' => 77096, 'sku' => 'NGO-T2092', 'attribute_set_id' => 4,
         'type_id' => 'simple', 'updated_at' => '2026-01-01 00:00:00',
         'created_in' => self::NOW + 1000, 'updated_in' => 2_147_483_647],
    ];

    private const CATEGORY_LINKS = [
        ['category_id' => 603, 'product_id' => 77096],
    ];

    /**
     * Sin columnas de versionado (Community, o Commerce sin Staging) el
     * comportamiento no cambia: una fila de entidad, una categoría. Si el
     * arreglo hubiera sido deduplicar en PHP en vez de filtrar por versión,
     * esta prueba pasaría igual y la de arriba también — por eso está la
     * tercera, que mira el WHERE.
     */
    public function testTheSingleVersionCaseIsUnaffected(): void
    {
        $selects = [];
        $reader = $this->makeReader(
            hasVersioning: false, versionRows: [self::VERSION_ROWS[1]], selects: $selects
        );

        $items = $this->payloadOf($reader->getBySku(1, ['NGO-T2092']))['items'];

        $this->assertSame([603], $items[0]['category_ids']);
    }

    /**
     * @param mixed[]|null $versionRows
     * @param list<JoiningFakeSelect> $selects
     */
    private function makeReader(
        bool $hasVersioning,
        array &$selects,
        ?array $versionRows = null
    ): ProductReader {
        $versionRows ??= self::VERSION_ROWS;

        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => match ($column) {
                'row_id' => $hasVersioning,
                'created_in', 'updated_in' => $hasVersioning,
                default => false,
            }
        );
        $connection->method('select')->willReturnCallback(
            static function () use (&$selects): JoiningFakeSelect {
                $select = new JoiningFakeSelect();
                $selects[] = $select;
                return $select;
            }
        );
        $connection->method('fetchAll')->willReturnCallback(
            fn (JoiningFakeSelect $select): array => $this->evaluate($select, $versionRows)
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new ProductReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource)
        );
    }

    /**
     * Ejecuta de verdad la consulta que arma ProductReader contra las filas de
     * fixture: el producto cartesiano del join, los `where()` reales y la
     * proyección de columnas. Solo se modelan las dos tablas que esta prueba
     * necesita; el resto (EAV, websites) devuelve vacío, igual que en
     * ProductReaderTest.
     *
     * @param mixed[] $versionRows
     * @return mixed[]
     */
    private function evaluate(JoiningFakeSelect $select, array $versionRows): array
    {
        $rows = match ($select->table) {
            'catalog_product_entity' => array_map(
                static fn (array $row): array => ['e' => $row],
                $versionRows
            ),
            'catalog_category_product' => $this->joinLinksToEntities($versionRows),
            default => [],
        };

        foreach ($select->wheres as $where) {
            $rows = array_values(array_filter(
                $rows,
                fn (array $row): bool => $this->conditionMatches($row, $where['cond'], $where['value'])
            ));
        }

        return array_map(
            static function (array $row) use ($select): array {
                $out = [];
                foreach ($select->columns as $alias => $expression) {
                    [$table, $column] = explode('.', $expression, 2);
                    $out[is_string($alias) ? $alias : $column] = $row[$table][$column] ?? null;
                }
                return $out;
            },
            $rows
        );
    }

    /**
     * El join real de `categoryIds()`: `e.entity_id = l.product_id`. Con tres
     * filas de versión del mismo entity_id, un enlace de categoría produce
     * TRES filas — que es exactamente el defecto.
     *
     * @param mixed[] $versionRows
     * @return mixed[]
     */
    private function joinLinksToEntities(array $versionRows): array
    {
        $rows = [];
        foreach (self::CATEGORY_LINKS as $link) {
            foreach ($versionRows as $entity) {
                if ((int) $entity['entity_id'] === (int) $link['product_id']) {
                    $rows[] = ['l' => $link, 'e' => $entity];
                }
            }
        }
        return $rows;
    }

    /** @param mixed[] $row */
    private function conditionMatches(array $row, string $cond, mixed $value): bool
    {
        if (preg_match('/^(\w+)\.(\w+) IN \(\?\)$/', $cond, $m) === 1) {
            return in_array($row[$m[1]][$m[2]], (array) $value, false);
        }
        if (preg_match('/^(\w+)\.(\w+)\s*(>=|<=|>|<|=)\s*\?$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]][$m[2]], $m[3], $value);
        }
        if (preg_match('/^(\w+)\.(\w+)\s*(<=|>=|<|>)\s*UNIX_TIMESTAMP\(\)$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]][$m[2]], $m[3], self::NOW);
        }

        throw new \RuntimeException("condición de prueba no reconocida: {$cond}");
    }

    private function compare(mixed $actual, string $op, mixed $expected): bool
    {
        return match ($op) {
            '>' => $actual > $expected,
            '>=' => $actual >= $expected,
            '<' => $actual < $expected,
            '<=' => $actual <= $expected,
            '=' => $actual == $expected,
        };
    }
}

/**
 * Doble de `Magento\Framework\DB\Select` que, a diferencia del de
 * ProductReaderTest, SÍ registra los join: es lo que permite evaluar la
 * consulta de categorías contra filas de fixture en vez de solo inspeccionar
 * los strings que arma.
 */
final class JoiningFakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var array<int|string, string> alias => expresión, ya con prefijo de tabla */
    public array $columns = [];

    /** @var list<array{cond: string, value: mixed}> */
    public array $wheres = [];

    public ?string $orderSpec = null;

    public ?int $limitCount = null;

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
        $this->addColumns((array) $tables, $columns);
        return $this;
    }

    /**
     * @param mixed $tables
     * @param string|array<int|string, string> $columns
     * @param mixed $schema
     */
    public function join($tables, $cond, $columns = '*', $schema = null): self
    {
        $this->addColumns((array) $tables, $columns);
        return $this;
    }

    /**
     * @param array<int|string, string> $tables
     * @param string|array<int|string, string> $columns
     */
    private function addColumns(array $tables, $columns): void
    {
        if (!is_array($columns)) {
            return;
        }
        $alias = array_key_first($tables);
        foreach ($columns as $name => $expression) {
            // Las columnas de ProductReader vienen ya calificadas ('e.sku');
            // las que no, se califican con el alias de su tabla.
            $this->columns[$name] = str_contains($expression, '.')
                ? $expression
                : $alias . '.' . $expression;
        }
    }

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[] = ['cond' => $cond, 'value' => $value];
        return $this;
    }

    public function order($spec): self
    {
        $this->orderSpec = is_array($spec) ? implode(',', $spec) : (string) $spec;
        return $this;
    }

    public function limit($count = null, $offset = null): self
    {
        $this->limitCount = $count !== null ? (int) $count : null;
        return $this;
    }
}
