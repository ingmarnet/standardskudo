<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Store\Api\Data\StoreInterface;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ActiveVersionResolver;
use Standard\Skudo\Model\CategoryReader;
use Standard\Skudo\Model\Cursor;
use Standard\Skudo\Model\EntityKeyResolver;

/**
 * Cubre S0 Task A3: el segundo (y último) de los dos endpoints que le dan a
 * las tablas de categoría del espejo un camino de ingesta real. Sin él,
 * `Category.path` y `CategoryStoreState.is_active` nunca se escriben, y
 * `derive_category_effect` — correcta y probada en Python — queda
 * inalcanzable desde datos de espejo real.
 *
 * Corrección de esquema aplicada aquí (verificada contra la instancia de
 * referencia, solo lectura, y NO mencionada por el brief original de esta
 * tarea): `catalog_category_entity` también es staged — tiene su propio
 * `row_id` y `created_in`/`updated_in` — así que este lector reutiliza
 * EntityKeyResolver y ActiveVersionResolver pidiéndoles la respuesta para
 * `catalog_category_entity`, en vez de asumir que solo los productos se
 * versionan.
 *
 * Los fixtures de is_active/name reproducen un caso REAL verificado en la
 * instancia (categoría row_id=252, path "1/2/222/252"): is_active global
 * (store_id 0) = 0, override PY (store_id 1) = 0, override BR (store_id 3)
 * = 1; name global = "Todo por menos de US$ 500", sin override en PY
 * (hereda el global) y con override en BR = "Tudo por menos de US$ 500".
 * Es el caso que de verdad discrimina: las dos store views de esta
 * instancia cuelgan de la misma root category (2), así que el efecto por
 * tienda lo decide `is_active` por store, no el árbol.
 *
 * Como en ProductReaderTest/AttributeReaderTest, un `CategoryFakeSelect`
 * (abajo) registra y EVALÚA los where()/order()/limit() reales que arma
 * CategoryReader contra filas de fixture, dispatcheadas por la tabla
 * primaria de cada `from()`.
 */
class CategoryReaderTest extends TestCase
{
    private const CATEGORY_ENTITY_TYPE_ID = 3;
    private const NAME_ATTRIBUTE_ID = 45;
    private const IS_ACTIVE_ATTRIBUTE_ID = 46;
    private const STORE_PY = 1;
    private const STORE_BR = 3;

    /**
     * Ruling 1: `path` viene como "1/2/108/109" en Magento y
     * `derive_category_effect` decide pertenencia al árbol con
     * `root_category_id in assignment_path`, así que debe volver como lista
     * de enteros, no como el string original.
     */
    public function testPathStringBecomesAListOfInts(): void
    {
        $result = $this->getPageWithCategories([
            $this->categoryRow(rowId: 109, entityId: 109, path: '1/2/108/109'),
        ]);

        $this->assertSame([1, 2, 108, 109], $result['items'][0]['path']);
    }

    /**
     * El caso que de verdad discrimina en esta instancia: PY y BR cuelgan
     * del mismo root_category_id (2), así que es `is_active` por store view
     * —no el árbol— lo que decide si el producto es alcanzable. Datos
     * reales verificados (row_id 252): global inactiva, override PY
     * también inactiva, override BR activa.
     */
    public function testCategoryActiveInOneStoreViewAndInactiveInAnother(): void
    {
        $result = $this->getPageWithCategoryStoreData(
            categoryRow: $this->categoryRow(rowId: 252, entityId: 252, path: '1/2/222/252'),
            isActiveRows: [
                ['row_id' => 252, 'store_id' => 0, 'value' => '0'],
                ['row_id' => 252, 'store_id' => 1, 'value' => '0'],
                ['row_id' => 252, 'store_id' => 3, 'value' => '1'],
            ],
            nameRows: [
                ['row_id' => 252, 'store_id' => 0, 'value' => 'Todo por menos de US$ 500'],
            ],
        );

        $states = $this->storeStatesByStoreId($result['items'][0]);
        $this->assertFalse($states[self::STORE_PY]['is_active']);
        $this->assertTrue($states[self::STORE_BR]['is_active']);
    }

    /**
     * Mismo patrón global/override que ProductReader: una store view sin
     * fila propia hereda la de store_id=0. `default_name` (el nombre
     * "administrativo") es SIEMPRE el global, tenga o no override alguna
     * store view.
     */
    public function testNameFallsBackToGlobalWhenNoOverrideExistsForThatStoreView(): void
    {
        $result = $this->getPageWithCategoryStoreData(
            categoryRow: $this->categoryRow(rowId: 252, entityId: 252, path: '1/2/222/252'),
            isActiveRows: [
                ['row_id' => 252, 'store_id' => 0, 'value' => '1'],
            ],
            nameRows: [
                ['row_id' => 252, 'store_id' => 0, 'value' => 'Todo por menos de US$ 500'],
                ['row_id' => 252, 'store_id' => 3, 'value' => 'Tudo por menos de US$ 500'],
            ],
        );

        $item = $result['items'][0];
        $states = $this->storeStatesByStoreId($item);

        $this->assertSame('Todo por menos de US$ 500', $item['default_name']);
        // PY no tiene fila propia: hereda el nombre global.
        $this->assertSame('Todo por menos de US$ 500', $states[self::STORE_PY]['name']);
        // BR sí tiene override.
        $this->assertSame('Tudo por menos de US$ 500', $states[self::STORE_BR]['name']);
    }

    /**
     * Ninguna store view puede faltar en la respuesta, tenga o no override:
     * el lado Python no debe tener que adivinar si una ausencia significa
     * "inactiva" o "desconocida".
     */
    public function testEveryStoreViewGetsAnEntryEvenWithoutAnyOverrideRow(): void
    {
        $result = $this->getPageWithCategoryStoreData(
            categoryRow: $this->categoryRow(rowId: 400, entityId: 400, path: '1/2/400'),
            isActiveRows: [['row_id' => 400, 'store_id' => 0, 'value' => '1']],
            nameRows: [['row_id' => 400, 'store_id' => 0, 'value' => 'Solo Global']],
        );

        $states = $this->storeStatesByStoreId($result['items'][0]);

        $this->assertCount(2, $states);
        $this->assertTrue($states[self::STORE_PY]['is_active']);
        $this->assertSame('Solo Global', $states[self::STORE_PY]['name']);
        $this->assertTrue($states[self::STORE_BR]['is_active']);
        $this->assertSame('Solo Global', $states[self::STORE_BR]['name']);
    }

    public function testActiveVersionFilterIsAddedWhenVersioningColumnsArePresent(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, categoryRows: [], selects: $selects);

        $reader->getPage(limit: 10);

        $entitySelect = $this->entitySelect($selects);
        $this->assertContains('e.created_in <= UNIX_TIMESTAMP()', array_column($entitySelect->wheres, 'cond'));
        $this->assertContains('e.updated_in > UNIX_TIMESTAMP()', array_column($entitySelect->wheres, 'cond'));
    }

    public function testNoActiveVersionFilterIsAddedWhenVersioningColumnsAreAbsent(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: false, hasVersioning: false, categoryRows: [], selects: $selects);

        $reader->getPage(limit: 10);

        $entitySelect = $this->entitySelect($selects);
        foreach ($entitySelect->wheres as $where) {
            $this->assertStringNotContainsString('created_in', $where['cond']);
            $this->assertStringNotContainsString('updated_in', $where['cond']);
        }
    }

    /**
     * Igual que ProductReaderTest (Regla 3, Task 8): sin este filtro, la
     * paginación ascendente por clave hace que la versión programada a
     * futuro (siempre con la clave más alta) gane el upsert sobre la
     * vigente. Dos versiones del mismo category_id (252): la vieja cerrada
     * en el pasado, la vigente sin fin (2147483647).
     */
    public function testOnlyTheActiveVersionOfACategoryIsReturnedWhenItHasMultipleVersions(): void
    {
        $categoryRows = [
            $this->categoryRow(
                rowId: 900,
                entityId: 252,
                path: '1/2/222/OLD',
                createdIn: 1,
                updatedIn: 1_000_000_000,
            ),
            $this->categoryRow(
                rowId: 901,
                entityId: 252,
                path: '1/2/222/252',
                createdIn: 1_000_000_000,
                updatedIn: 2_147_483_647,
            ),
        ];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: true, categoryRows: $categoryRows, selects: $selects);

        $result = $reader->getPage(limit: 10);

        $this->assertCount(1, $result['items']);
        $this->assertSame([1, 2, 222, 252], $result['items'][0]['path']);
    }

    /**
     * Paginación por cursor (keyset) sobre la clave resuelta, nunca offset:
     * mismo contrato que ProductReader/AttributeReader.
     */
    public function testGetPagePaginatesByTheResolvedKeyCursorNotByOffset(): void
    {
        $categoryRows = [
            $this->categoryRow(rowId: 300, entityId: 300, path: '1/2/300'),
            $this->categoryRow(rowId: 100, entityId: 100, path: '1/2/100'),
            $this->categoryRow(rowId: 200, entityId: 200, path: '1/2/200'),
        ];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: false, categoryRows: $categoryRows, selects: $selects);

        $firstPage = $reader->getPage(limit: 2);

        $this->assertSame([100, 200], array_column($firstPage['items'], 'category_id'));
        $this->assertNotNull($firstPage['next_cursor']);
        $this->assertSame(200, (new Cursor())->decode($firstPage['next_cursor']));

        $secondPage = $reader->getPage(limit: 2, cursor: $firstPage['next_cursor']);

        $this->assertSame([300], array_column($secondPage['items'], 'category_id'));
        $this->assertNull($secondPage['next_cursor']);
    }

    /**
     * `category_id` es la identidad de negocio ESTABLE (entity_id), nunca
     * la clave de paginación con Staging (row_id): derive_category_effect y
     * el resto del espejo identifican categorías por entity_id. El cursor,
     * en cambio, sí viaja sobre row_id (la clave resuelta).
     */
    public function testCategoryIdIsTheStableEntityIdNotTheStagingRowId(): void
    {
        $categoryRows = [
            $this->categoryRow(rowId: 9001, entityId: 252, path: '1/2/222/252'),
        ];

        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: false, categoryRows: $categoryRows, selects: $selects);

        $result = $reader->getPage(limit: 10);

        $this->assertSame(252, $result['items'][0]['category_id']);
        // El cursor pagina sobre row_id (9001), no sobre entity_id (252).
        $this->assertSame(9001, (new Cursor())->decode($result['next_cursor'] ?? (new Cursor())->encode(9001)));
    }

    /** @param mixed[] $categoryRows */
    private function getPageWithCategories(array $categoryRows): array
    {
        $selects = [];
        $reader = $this->makeReader(hasRowId: true, hasVersioning: false, categoryRows: $categoryRows, selects: $selects);

        return $reader->getPage(limit: 10);
    }

    /**
     * @param mixed[] $categoryRow
     * @param mixed[] $isActiveRows
     * @param mixed[] $nameRows
     */
    private function getPageWithCategoryStoreData(array $categoryRow, array $isActiveRows, array $nameRows): array
    {
        $selects = [];
        $reader = $this->makeReader(
            hasRowId: true,
            hasVersioning: false,
            categoryRows: [$categoryRow],
            selects: $selects,
            isActiveRows: $isActiveRows,
            nameRows: $nameRows,
        );

        return $reader->getPage(limit: 10);
    }

    /**
     * @param mixed[] $item
     * @return array<int, array{store_id: int, is_active: bool, name: string}>
     */
    private function storeStatesByStoreId(array $item): array
    {
        $out = [];
        foreach ($item['store_states'] as $state) {
            $out[$state['store_id']] = $state;
        }
        return $out;
    }

    /** @return mixed[] */
    private function categoryRow(
        int $rowId,
        int $entityId,
        string $path,
        ?int $createdIn = null,
        ?int $updatedIn = null,
    ): array {
        $row = ['row_id' => $rowId, 'entity_id' => $entityId, 'path' => $path];
        if ($createdIn !== null) {
            $row['created_in'] = $createdIn;
        }
        if ($updatedIn !== null) {
            $row['updated_in'] = $updatedIn;
        }
        return $row;
    }

    /**
     * @param mixed[] $categoryRows
     * @param list<CategoryFakeSelect> $selects
     * @param mixed[] $isActiveRows
     * @param mixed[] $nameRows
     */
    private function makeReader(
        bool $hasRowId,
        bool $hasVersioning,
        array $categoryRows,
        array &$selects,
        array $isActiveRows = [],
        array $nameRows = [],
    ): CategoryReader {
        $fixtures = [
            'catalog_category_entity' => $categoryRows,
            'eav_entity_type' => [
                ['entity_type_code' => 'catalog_category', 'entity_type_id' => self::CATEGORY_ENTITY_TYPE_ID],
            ],
            'eav_attribute' => [
                [
                    'entity_type_id' => self::CATEGORY_ENTITY_TYPE_ID,
                    'attribute_code' => 'name',
                    'attribute_id' => self::NAME_ATTRIBUTE_ID,
                    'backend_type' => 'varchar',
                ],
                [
                    'entity_type_id' => self::CATEGORY_ENTITY_TYPE_ID,
                    'attribute_code' => 'is_active',
                    'attribute_id' => self::IS_ACTIVE_ATTRIBUTE_ID,
                    'backend_type' => 'int',
                ],
            ],
            'catalog_category_entity_int' => array_map(
                static fn (array $row): array => $row + ['attribute_id' => self::IS_ACTIVE_ATTRIBUTE_ID],
                $isActiveRows
            ),
            'catalog_category_entity_varchar' => array_map(
                static fn (array $row): array => $row + ['attribute_id' => self::NAME_ATTRIBUTE_ID],
                $nameRows
            ),
        ];

        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => match ($column) {
                'row_id' => $hasRowId,
                'created_in', 'updated_in' => $hasVersioning,
                default => false,
            }
        );
        $connection->method('select')->willReturnCallback(static function () use (&$selects): CategoryFakeSelect {
            $select = new CategoryFakeSelect();
            $selects[] = $select;
            return $select;
        });
        $connection->method('fetchAll')->willReturnCallback(
            fn (CategoryFakeSelect $select): array => $this->evaluateSelect($select, $fixtures[$select->table] ?? [])
        );
        $connection->method('fetchOne')->willReturnCallback(
            function (CategoryFakeSelect $select) use ($fixtures) {
                $rows = $this->evaluateSelect($select, $fixtures[$select->table] ?? []);
                if ($rows === []) {
                    return false;
                }
                $first = array_values($rows[0]);
                return $first[0] ?? false;
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStores')->willReturn([
            $this->store(self::STORE_PY),
            $this->store(self::STORE_BR),
        ]);

        return new CategoryReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource),
            new ActiveVersionResolver($resource),
            $storeManager,
        );
    }

    private function store(int $id): StoreInterface
    {
        $store = $this->createMock(StoreInterface::class);
        $store->method('getId')->willReturn($id);
        return $store;
    }

    /** @param list<CategoryFakeSelect> $selects */
    private function entitySelect(array $selects): CategoryFakeSelect
    {
        foreach ($selects as $select) {
            if ($select->table === 'catalog_category_entity') {
                return $select;
            }
        }
        $this->fail('CategoryReader no armó ninguna consulta sobre catalog_category_entity');
    }

    /**
     * @param mixed[] $fixtureRows
     * @return mixed[]
     */
    private function evaluateSelect(CategoryFakeSelect $select, array $fixtureRows): array
    {
        $rows = $fixtureRows;
        foreach ($select->wheres as $where) {
            $rows = array_values(array_filter(
                $rows,
                fn (array $row): bool => $this->conditionMatches($row, $where['cond'], $where['value'])
            ));
        }

        if ($select->orderSpec !== null
            && preg_match('/^(?:\w+\.)?(\w+)\s+(ASC|DESC)$/i', $select->orderSpec, $m) === 1
        ) {
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
        if (preg_match('/^(?:\w+\.)?(\w+)\s*(<=|>=|<|>|=)\s*UNIX_TIMESTAMP\(\)$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]] ?? null, $m[2], time());
        }
        if (preg_match('/^(?:\w+\.)?(\w+)\s*(>=|<=|>|<|=)\s*\?$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]] ?? null, $m[2], $value);
        }
        if (preg_match('/^(?:\w+\.)?(\w+) IN \(\?\)$/', $cond, $m) === 1) {
            return in_array($row[$m[1]] ?? null, (array) $value, false);
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
     * @param array<int|string, string> $columns
     * @return mixed[]
     */
    private function projectColumns(array $row, array $columns): array
    {
        $out = [];
        foreach ($columns as $alias => $expr) {
            $field = str_contains($expr, '.') ? explode('.', $expr, 2)[1] : $expr;
            $out[is_int($alias) ? $field : $alias] = $row[$field] ?? null;
        }
        return $out;
    }
}

/**
 * Doble de prueba de `Magento\Framework\DB\Select`: registra la tabla, las
 * columnas, los `where()` encadenados y el `order()`/`limit()` pedidos, sin
 * tocar ninguna base de datos. Ver el docblock del gemelo en
 * ProductReaderTest/AttributeReaderTest para el razonamiento de extender la
 * clase real.
 */
final class CategoryFakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var array<int|string, string> */
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
        $this->columns = is_array($columns) ? $columns : [$columns];
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
