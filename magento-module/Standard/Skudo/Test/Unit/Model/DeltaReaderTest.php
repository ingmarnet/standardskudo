<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\DeltaReader;

/**
 * Cubre la paginación por change_id (monótona, nunca por timestamp: dos
 * cambios en el mismo segundo se perderían si se paginara por changed_at) y
 * la Ruling 2 de S0 Task 10: con Magento_Staging activo, una actualización
 * programada crea su fila de versión al programarse y se activa más tarde,
 * cuando created_in pasa — sin que se dispare ningún evento. Un observer no
 * puede detectar el paso del tiempo, así que DeltaReader debe unir esos SKUs
 * a la respuesta cuando el caller pasa `sinceTimestamp`.
 *
 * Como en ProductReaderTest, un `DeltaFakeSelect` (abajo) registra y EVALÚA
 * los where()/order()/limit() reales que arma DeltaReader contra filas de
 * fixture, para que una regresión que borre o rompa el filtro de activación
 * falle con datos concretos, no con una comparación de strings.
 */
class DeltaReaderTest extends TestCase
{
    private const NOW = 2_000_000_000;

    public function testGetChangesReturnsQueueRowsOrderedByChangeIdAboveSinceId(): void
    {
        $queueRows = [
            ['change_id' => 5, 'sku' => 'A', 'event' => 'save', 'changed_at' => '2026-01-01 00:00:00'],
            ['change_id' => 7, 'sku' => 'B', 'event' => 'delete', 'changed_at' => '2026-01-01 00:00:05'],
        ];

        $selects = [];
        $reader = $this->makeReader(hasVersioning: false, queueRows: $queueRows, entityRows: [], selects: $selects);

        $result = $reader->getChanges(sinceId: 2, limit: 10);

        $this->assertSame(
            [
                ['change_id' => 5, 'sku' => 'A', 'event' => 'save', 'changed_at' => '2026-01-01 00:00:00'],
                ['change_id' => 7, 'sku' => 'B', 'event' => 'delete', 'changed_at' => '2026-01-01 00:00:05'],
            ],
            $result['items']
        );
        $this->assertSame(7, $result['last_change_id']);
    }

    /**
     * Ruling 2: columnas ausentes (Community, o Commerce sin Staging). Si se
     * quitara por completo el código de activación de versión, esta prueba
     * seguiría pasando en el resultado final (nada extra), pero fallaría
     * igual porque ya no se llamaría tableColumnExists(): eso es lo que
     * prueba que la rama realmente se evaluó y se descartó, no que nunca
     * existió.
     */
    public function testNoVersionActivationQueryWhenVersioningColumnsAreAbsent(): void
    {
        $entityRows = [
            // Coincidiría con la ventana de activación si la rama se evaluara
            // igual; está acá para probar que, con columnas ausentes, ni
            // siquiera se llega a ejecutar esa consulta.
            ['sku' => 'WOULD-MATCH', 'created_in' => self::NOW - 100, 'updated_in' => 2_147_483_647],
        ];

        $selects = [];
        $reader = $this->makeReader(hasVersioning: false, queueRows: [], entityRows: $entityRows, selects: $selects);

        $result = $reader->getChanges(sinceId: 0, limit: 10, sinceTimestamp: self::NOW - 200);

        $this->assertSame([], $result['items'], 'no debería agregarse nada cuando el esquema no tiene versionado');
        $this->assertFalse(
            $this->hasSelectForTable($selects, 'catalog_product_entity'),
            'no debería ejecutarse ninguna consulta sobre catalog_product_entity cuando las columnas no existen'
        );
    }

    /**
     * Ruling 2: columnas presentes y sinceTimestamp captura una versión
     * activada dentro de la ventana. Si se quitara la consulta de
     * activación, el SKU nunca aparecería: esta prueba fallaría de verdad.
     */
    public function testVersionActivationWithinWindowIsIncludedAsSaveWithChangeIdZero(): void
    {
        $entityRows = [
            ['sku' => 'NEW-VERSION', 'created_in' => self::NOW - 100, 'updated_in' => 2_147_483_647],
        ];

        $selects = [];
        $reader = $this->makeReader(hasVersioning: true, queueRows: [], entityRows: $entityRows, selects: $selects);

        $result = $reader->getChanges(sinceId: 0, limit: 10, sinceTimestamp: self::NOW - 200);

        $this->assertSame(
            [
                [
                    'change_id' => 0,
                    'sku' => 'NEW-VERSION',
                    'event' => 'save',
                    'changed_at' => gmdate('Y-m-d H:i:s', self::NOW - 100),
                ],
            ],
            $result['items']
        );
        // El watermark de change_id no debe moverse por una fila con change_id 0.
        $this->assertNull($result['last_change_id']);
    }

    /**
     * Ruling 2: columnas presentes pero sinceTimestamp ya vio la activación
     * (la ventana quedó fuera del rango created_in > sinceTimestamp). La
     * consulta de activación SÍ debe ejecutarse (se afirma explícitamente
     * abajo) y filtrar correctamente a cero filas; si se quitara la consulta
     * entera, el resultado final sería el mismo (nada extra) pero la
     * aserción sobre `$selects` fallaría, así que esta prueba sí distingue
     * "no hay nada que agregar" de "nunca se preguntó".
     */
    public function testVersionActivationOutsideWindowAddsNothingExtra(): void
    {
        $entityRows = [
            ['sku' => 'ALREADY-SEEN', 'created_in' => self::NOW - 100, 'updated_in' => 2_147_483_647],
        ];

        $selects = [];
        $reader = $this->makeReader(hasVersioning: true, queueRows: [], entityRows: $entityRows, selects: $selects);

        $result = $reader->getChanges(sinceId: 0, limit: 10, sinceTimestamp: self::NOW - 50);

        $this->assertSame([], $result['items']);
        $this->assertTrue(
            $this->hasSelectForTable($selects, 'catalog_product_entity'),
            'la consulta de activación debió ejecutarse (y filtrar a cero filas), no omitirse'
        );
    }

    /**
     * @param mixed[] $queueRows
     * @param mixed[] $entityRows
     * @param list<DeltaFakeSelect> $selects
     */
    private function makeReader(
        bool $hasVersioning,
        array $queueRows,
        array $entityRows,
        array &$selects
    ): DeltaReader {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => match ($column) {
                'created_in', 'updated_in' => $hasVersioning,
                default => false,
            }
        );
        $connection->method('select')->willReturnCallback(static function () use (&$selects): DeltaFakeSelect {
            $select = new DeltaFakeSelect();
            $selects[] = $select;
            return $select;
        });
        $connection->method('fetchAll')->willReturnCallback(
            fn (DeltaFakeSelect $select): array => $this->evaluateSelect($select, $queueRows, $entityRows)
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new DeltaReader($resource);
    }

    /** @param list<DeltaFakeSelect> $selects */
    private function hasSelectForTable(array $selects, string $table): bool
    {
        foreach ($selects as $select) {
            if ($select->table === $table) {
                return true;
            }
        }
        return false;
    }

    /**
     * @param mixed[] $queueRows
     * @param mixed[] $entityRows
     * @return mixed[]
     */
    private function evaluateSelect(DeltaFakeSelect $select, array $queueRows, array $entityRows): array
    {
        $rows = match ($select->table) {
            'standard_skudo_change_log' => $queueRows,
            'catalog_product_entity' => $entityRows,
            default => [],
        };

        foreach ($select->wheres as $where) {
            $rows = array_values(array_filter(
                $rows,
                fn (array $row): bool => $this->conditionMatches($row, $where['cond'], $where['value'])
            ));
        }

        if ($select->orderSpec !== null && preg_match('/^(\w+)\s+(ASC|DESC)$/i', $select->orderSpec, $m) === 1) {
            $field = $m[1];
            $direction = strtoupper($m[2]);
            usort($rows, static fn (array $a, array $b): int => $direction === 'ASC'
                ? $a[$field] <=> $b[$field]
                : $b[$field] <=> $a[$field]);
        }

        if ($select->limitCount !== null) {
            $rows = array_slice($rows, 0, $select->limitCount);
        }

        return $rows;
    }

    private function conditionMatches(array $row, string $cond, mixed $value): bool
    {
        if (preg_match('/^(\w+)\s*(>=|<=|>|<|=)\s*\?$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]], $m[2], $value);
        }
        if (preg_match('/^(\w+)\s*(<=|>=|<|>)\s*UNIX_TIMESTAMP\(\)$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]], $m[2], self::NOW);
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
 * Doble de prueba de `Magento\Framework\DB\Select` para DeltaReaderTest:
 * registra tabla, where()/order()/limit() sin tocar ninguna base de datos.
 * Ver la clase homónima en ProductReaderTest para el razonamiento completo
 * de por qué se extiende la clase real en vez de solo imitar su interfaz.
 */
final class DeltaFakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

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
