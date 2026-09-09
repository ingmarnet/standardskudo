<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Exception\InputException;
use PHPUnit\Framework\TestCase;
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

        return new ProductReader($resource, new Cursor(), new EntityKeyResolver($resource));
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
     * `where()` reales que armó ProductReader contra las filas de ejemplo.
     * Cualquier otra tabla (EAV/website/category) no es objeto de esta
     * prueba y devuelve vacío.
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
 * columnas y los `where()` encadenados, sin tocar ninguna base de datos.
 */
final class FakeSelect
{
    public string $table = '';

    /** @var array<string, string> */
    public array $columns = [];

    /** @var list<array{cond: string, value: mixed}> */
    public array $wheres = [];

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     */
    public function from(mixed $tables, string|array $columns = '*'): self
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
     */
    public function join(mixed $tables, string $cond, string|array $columns = []): self
    {
        return $this;
    }

    public function where(string $cond, mixed $value = null): self
    {
        $this->wheres[] = ['cond' => $cond, 'value' => $value];
        return $this;
    }

    public function order(mixed $spec): self
    {
        return $this;
    }

    public function limit(mixed $count, mixed $offset = 0): self
    {
        return $this;
    }
}
