<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Exception\InputException;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ActiveVersionResolver;
use Standard\Skudo\Model\Cursor;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\ProductReader;

/**
 * Cubre la Regla 3 (S0 Task 8): `catalog_product_entity` con Magento_Staging
 * activo no tiene una fila por producto sino una por VERSIÓN, acotada por
 * created_in/updated_in. Sin filtrar por la versión activa, la paginación
 * ascendente por clave hace que una versión programada a futuro (que
 * siempre tiene la clave más alta) gane el upsert sobre la versión vigente.
 *
 * Como PHPUnit aquí no tiene una base de datos real, estos tests usan un
 * `FakeSelect` (abajo) que registra los `where()` que arma ProductReader y,
 * para la prueba de "solo la versión activa vuelve", evalúa esas mismas
 * condiciones contra filas de ejemplo — así una regresión que borre el
 * filtro (o lo escriba mal) hace fallar la prueba de verdad, no solo una
 * comparación de strings.
 */
class ProductReaderTest extends TestCase
{
    public function testActiveVersionFilterIsAddedWhenVersioningColumnsArePresent(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, fixtureRows: [], selects: $selects);

        $reader->getPage(storeId: 1, limit: 10);

        $entitySelect = $this->entitySelect($selects);
        $this->assertWhereConditionExists($entitySelect, 'e.created_in <= UNIX_TIMESTAMP()');
        $this->assertWhereConditionExists($entitySelect, 'e.updated_in > UNIX_TIMESTAMP()');
    }

    public function testNoActiveVersionFilterIsAddedWhenVersioningColumnsAreAbsent(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: false, hasVersioning: false, fixtureRows: [], selects: $selects);

        $reader->getPage(storeId: 1, limit: 10);

        $entitySelect = $this->entitySelect($selects);
        foreach ($entitySelect->wheres as $where) {
            $this->assertStringNotContainsString('created_in', $where['cond']);
            $this->assertStringNotContainsString('updated_in', $where['cond']);
        }
    }

    public function testOnlyTheActiveVersionIsReturnedForASkuWithMultipleVersions(): void
    {
        // Reproduce el caso real verificado en la instancia de referencia:
        // SKU NGO-T2092, entity_id 77096, con tres versiones (aquí, dos
        // alcanzan para probar el punto). La versión vieja quedó cerrada
        // en updated_in=1000000000 (año 2001, seguro en el pasado); la
        // activa tiene updated_in=2147483647 (el centinela "sin fin").
        $fixtureRows = [
            [
                'row_id' => 288062, 'entity_id' => 77096, 'sku' => 'NGO-T2092',
                'attribute_set_id' => 4, 'type_id' => 'simple',
                'updated_at' => '2026-01-01 00:00:00',
                'created_in' => 1, 'updated_in' => 1_000_000_000,
            ],
            [
                'row_id' => 288063, 'entity_id' => 77096, 'sku' => 'NGO-T2092',
                'attribute_set_id' => 4, 'type_id' => 'simple',
                'updated_at' => '2026-06-01 00:00:00',
                'created_in' => 1_000_000_000, 'updated_in' => 2_147_483_647,
            ],
        ];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, fixtureRows: $fixtureRows, selects: $selects);

        $result = $reader->getBySku(1, ['NGO-T2092']);

        $this->assertCount(1, $result['items']);
        $this->assertSame('NGO-T2092', $result['items'][0]['sku']);
        $this->assertSame('2026-06-01 00:00:00', $result['items'][0]['updated_at']);
    }

    public function testGetBySkuRejectsMoreThanOneHundredSkus(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, fixtureRows: [], selects: $selects);

        $skus = array_map(static fn (int $i): string => "SKU-{$i}", range(1, 101));

        $this->expectException(InputException::class);
        $reader->getBySku(1, $skus);
    }

    public function testGetBySkuAcceptsExactlyOneHundredSkus(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, fixtureRows: [], selects: $selects);

        $skus = array_map(static fn (int $i): string => "SKU-{$i}", range(1, 100));

        // 100 no debe rechazarse: solo lo que excede el tope. El fixture está
        // vacío a propósito, así que basta con que no lance InputException.
        $result = $reader->getBySku(1, $skus);

        $this->assertSame(['items' => []], $result);
    }

    /**
     * Regla 3/Finding 2 de revisión: la paginación por keyset SOLO es
     * correcta si el orden coincide con la dirección del cursor. El fixture
     * se inserta deliberadamente desordenado (clave 30, 10, 20); si
     * ProductReader no ordenara por la clave resuelta (o la invirtiera), el
     * `FakeSelect` reproduciría ese mismo desorden (o el orden inverso) al
     * evaluar los `where()`/`order()` reales, y esta prueba fallaría con
     * datos concretos, no con una comparación de strings.
     */
    public function testGetPageOrdersRowsAscendingByTheResolvedKey(): void
    {
        $fixtureRows = [
            $this->entityRow(30, 'C'),
            $this->entityRow(10, 'A'),
            $this->entityRow(20, 'B'),
        ];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: false, fixtureRows: $fixtureRows, selects: $selects);

        $result = $reader->getPage(storeId: 1, limit: 10);

        $this->assertSame(['A', 'B', 'C'], array_column($result['items'], 'sku'));
    }

    /**
     * Finding 2 de revisión: una página llena (tantas filas como el límite)
     * debe devolver un `next_cursor` no nulo, y ese cursor debe apuntar a la
     * clave de la última fila devuelta. Si `next_cursor` se calculara al
     * revés, `iter_products` del cliente Python cortaría el barrido a mitad
     * de catálogo sin avisar.
     */
    public function testGetPageReturnsNonNullNextCursorWhenThePageIsFull(): void
    {
        $fixtureRows = [$this->entityRow(10, 'A'), $this->entityRow(20, 'B')];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: false, fixtureRows: $fixtureRows, selects: $selects);

        $result = $reader->getPage(storeId: 1, limit: 2);

        $this->assertCount(2, $result['items']);
        $this->assertNotNull($result['next_cursor']);
        $this->assertSame(20, (new Cursor())->decode($result['next_cursor']));
    }

    /**
     * Finding 2 de revisión: una página corta (menos filas que el límite)
     * debe devolver `next_cursor: null`. Si esto se invirtiera, el cliente
     * Python (ver `iter_products`) pediría una página más, siempre vacía,
     * en un bucle infinito.
     */
    public function testGetPageReturnsNullNextCursorWhenThePageIsShort(): void
    {
        $fixtureRows = [$this->entityRow(10, 'A'), $this->entityRow(20, 'B')];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: false, fixtureRows: $fixtureRows, selects: $selects);

        $result = $reader->getPage(storeId: 1, limit: 5);

        $this->assertCount(2, $result['items']);
        $this->assertNull($result['next_cursor']);
    }

    public function testGetBySkuWithNoSkusReturnsEmptyItemsWithoutQuerying(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, fixtureRows: [], selects: $selects);

        $result = $reader->getBySku(1, []);

        $this->assertSame(['items' => []], $result);
        $this->assertSame([], $selects, 'no debería haber ejecutado ninguna consulta');
    }

    /**
     * @param mixed[] $fixtureRows
     * @param list<FakeSelect> $selects
     */
    private function makeReader(bool $hasRowId, bool $hasVersioning, array $fixtureRows, array &$selects): ProductReader
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => match ($column) {
                'row_id' => $hasRowId,
                'created_in', 'updated_in' => $hasVersioning,
                default => false,
            }
        );
        $connection->method('select')->willReturnCallback(static function () use (&$selects): FakeSelect {
            $select = new FakeSelect();
            $selects[] = $select;
            return $select;
        });
        $connection->method('fetchAll')->willReturnCallback(
            fn (FakeSelect $select): array => $this->evaluateEntitySelect($select, $fixtureRows)
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new ProductReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource),
            new ActiveVersionResolver($resource)
        );
    }

    /**
     * Fila de fixture mínima de catalog_product_entity, sin columnas de
     * versionado (para las pruebas de orden/paginación, que no ejercitan la
     * Regla 3 y no la necesitan).
     *
     * @return mixed[]
     */
    private function entityRow(int $rowId, string $sku): array
    {
        return [
            'row_id' => $rowId,
            'entity_id' => $rowId,
            'sku' => $sku,
            'attribute_set_id' => 4,
            'type_id' => 'simple',
            'updated_at' => '2026-01-01 00:00:00',
        ];
    }

    /** @param list<FakeSelect> $selects */
    private function entitySelect(array $selects): FakeSelect
    {
        foreach ($selects as $select) {
            if ($select->table === 'catalog_product_entity') {
                return $select;
            }
        }
        $this->fail('ProductReader no armó ninguna consulta sobre catalog_product_entity');
    }

    private function assertWhereConditionExists(FakeSelect $select, string $expectedCond): void
    {
        $this->assertContains($expectedCond, array_column($select->wheres, 'cond'));
    }

    /**
     * Simula la ejecución del SELECT sobre catalog_product_entity: aplica los
     * `where()`, el `order()` y el `limit()` reales que armó ProductReader
     * contra las filas de ejemplo — no solo inspecciona los strings que
     * arma, sino que los ejecuta, para que un `order()` borrado o invertido,
     * o un `limit()` no aplicado, hagan fallar la prueba con datos reales en
     * vez de pasar por casualidad. Cualquier otra tabla (EAV/website/
     * category) no es objeto de esta prueba y devuelve vacío.
     *
     * @param mixed[] $fixtureRows
     * @return mixed[]
     */
    private function evaluateEntitySelect(FakeSelect $select, array $fixtureRows): array
    {
        if ($select->table !== 'catalog_product_entity') {
            return [];
        }

        $rows = $fixtureRows;
        foreach ($select->wheres as $where) {
            $rows = array_values(array_filter(
                $rows,
                fn (array $row): bool => $this->conditionMatches($row, $where['cond'], $where['value'])
            ));
        }

        if ($select->orderSpec !== null && preg_match('/^e\.(\w+)\s+(ASC|DESC)$/i', $select->orderSpec, $m) === 1) {
            $field = $m[1];
            $direction = strtoupper($m[2]);
            usort($rows, static fn (array $a, array $b): int => $direction === 'ASC'
                ? $a[$field] <=> $b[$field]
                : $b[$field] <=> $a[$field]);
        }

        if ($select->limitCount !== null) {
            $rows = array_slice($rows, 0, $select->limitCount);
        }

        return array_map(fn (array $row): array => $this->projectColumns($row, $select->columns), $rows);
    }

    private function conditionMatches(array $row, string $cond, mixed $value): bool
    {
        if (preg_match('/^e\.(\w+)\s*(>=|<=|>|<|=)\s*\?$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]], $m[2], $value);
        }
        if (preg_match('/^e\.(\w+) IN \(\?\)$/', $cond, $m) === 1) {
            return in_array($row[$m[1]], (array) $value, false);
        }
        if (preg_match('/^e\.(\w+)\s*(<=|>=|<|>)\s*UNIX_TIMESTAMP\(\)$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]], $m[2], time());
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

    /**
     * @param mixed[] $row
     * @param array<string, string> $columns
     * @return mixed[]
     */
    private function projectColumns(array $row, array $columns): array
    {
        $out = [];
        foreach ($columns as $alias => $expr) {
            $field = str_contains($expr, '.') ? explode('.', $expr, 2)[1] : $expr;
            $out[$alias] = $row[$field] ?? null;
        }
        return $out;
    }
}

/**
 * Doble de prueba de `Magento\Framework\DB\Select`: registra la tabla, las
 * columnas, los `where()` encadenados y el `order()` pedido, sin tocar
 * ninguna base de datos.
 *
 * Extiende la clase real (en vez de solo imitar su interfaz) para que
 * `ProductReader::baseEntitySelect()`/`applyActiveVersionFilter()` puedan
 * tener el tipo `Select` en su firma sin romper estas pruebas. El
 * constructor de `Select` exige un adaptador real y un `SelectRenderer`;
 * como este doble sobreescribe todos los métodos que `ProductReader` usa
 * (from/join/where/order/limit) y ninguno de ellos llama a `parent::`,
 * omitir `parent::__construct()` es seguro: no queda ningún estado heredado
 * del que este doble dependa.
 */
final class FakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var array<string, string> */
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
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        $this->table = (string) array_values((array) $tables)[0];
        $this->columns = is_array($columns) ? $columns : [$columns];
        return $this;
    }

    /**
     * Las subconsultas EAV/website/category no son objeto de estas pruebas;
     * basta con mantener el encadenado fluido.
     *
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
        $this->orderSpec = is_array($spec) ? implode(',', $spec) : (string) $spec;
        return $this;
    }

    public function limit($count = null, $offset = null): self
    {
        $this->limitCount = $count !== null ? (int) $count : null;
        return $this;
    }
}
