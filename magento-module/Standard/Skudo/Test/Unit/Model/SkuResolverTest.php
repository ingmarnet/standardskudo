<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\SkuResolver;

/**
 * H1: los eventos de escritura masiva de Magento llevan IDS, y la cola de
 * cambios guarda SKUS. Esta clase es la traducción, y la prueba que importa
 * es la primera: los ids de esos eventos son `entity_id`, NUNCA la clave de
 * la tabla. Bajo Magento_Staging la clave es `row_id`, y confundirlas no
 * falla: resuelve el SKU de OTRO producto, y la cola queda con un cambio
 * registrado sobre un producto que nadie tocó mientras el que sí cambió
 * nunca se refresca. Es el modo de fallo silencioso que H1 existe para
 * cerrar, reproducido dentro del arreglo.
 */
class SkuResolverTest extends TestCase
{
    /**
     * La fixture es deliberadamente adversa: el `row_id` de un producto
     * (1001) es TAMBIÉN el `entity_id` de otro. Un resolutor que empareje
     * por la clave de la tabla devuelve 'SKU-DE-LA-FILA-1001'; el correcto
     * devuelve 'SKU-ENTIDAD-1001'.
     */
    public function testIdsAreMatchedByEntityIdAndNeverByTheEntityKey(): void
    {
        $resolver = $this->makeResolver(hasRowId: true, rows: [
            ['row_id' => 101, 'entity_id' => 1001, 'sku' => 'SKU-ENTIDAD-1001'],
            ['row_id' => 1001, 'entity_id' => 5001, 'sku' => 'SKU-DE-LA-FILA-1001'],
        ]);

        $resolution = $resolver->resolve([1001]);

        $this->assertSame(['SKU-ENTIDAD-1001'], $resolution->skus);
        $this->assertSame([], $resolution->unresolvedIds);
    }

    /**
     * Sin Magento_Staging la clave ES `entity_id` y la traducción tiene que
     * seguir funcionando igual: el módulo es cross-edition y nada ramifica
     * por edición.
     */
    public function testItResolvesTheSameWayWhenTheEntityKeyIsEntityId(): void
    {
        $resolver = $this->makeResolver(hasRowId: false, rows: [
            ['entity_id' => 7, 'sku' => 'SKU-SIETE'],
            ['entity_id' => 8, 'sku' => 'SKU-OCHO'],
        ]);

        $resolution = $resolver->resolve([8, 7]);

        $this->assertSame(['SKU-OCHO', 'SKU-SIETE'], $resolution->skus);
        $this->assertSame([], $resolution->unresolvedIds);
    }

    /**
     * Un id que no resuelve es un cambio PERDIDO, no una fila de menos: si
     * se descarta en silencio, el espejo sostiene el valor viejo para
     * siempre. El resolutor lo devuelve nombrado para que el observer pueda
     * dejar rastro.
     */
    public function testAnIdWithNoRowIsReportedAsUnresolved(): void
    {
        $resolver = $this->makeResolver(hasRowId: false, rows: [
            ['entity_id' => 7, 'sku' => 'SKU-SIETE'],
        ]);

        $resolution = $resolver->resolve([7, 999]);

        $this->assertSame(['SKU-SIETE'], $resolution->skus);
        $this->assertSame([999], $resolution->unresolvedIds);
    }

    /**
     * Bajo versionado, un `entity_id` puede tener más de una fila. Se
     * devuelven TODOS los SKUs distintos: registrar uno de sobra cuesta un
     * refresco redundante (el camino de refresco es upsert), perder el que
     * sí cambió cuesta un valor rancio permanente. La asimetría decide.
     */
    public function testEveryDistinctSkuOfAVersionedEntityIsReturnedOnce(): void
    {
        $resolver = $this->makeResolver(hasRowId: true, rows: [
            ['row_id' => 10, 'entity_id' => 3, 'sku' => 'SKU-VIEJO'],
            ['row_id' => 11, 'entity_id' => 3, 'sku' => 'SKU-NUEVO'],
            ['row_id' => 12, 'entity_id' => 3, 'sku' => 'SKU-VIEJO'],
        ]);

        $resolution = $resolver->resolve([3]);

        $this->assertSame(['SKU-NUEVO', 'SKU-VIEJO'], $resolution->skus);
        $this->assertSame([], $resolution->unresolvedIds);
    }

    public function testAnEmptyIdListAsksTheDatabaseNothing(): void
    {
        $resolver = $this->makeResolver(hasRowId: false, rows: [
            ['entity_id' => 7, 'sku' => 'SKU-SIETE'],
        ], queries: $queries);

        $resolution = $resolver->resolve([]);

        $this->assertSame([], $resolution->skus);
        $this->assertSame([], $resolution->unresolvedIds);
        $this->assertSame(0, $queries, 'una lista vacía no debe consultar la base');
    }

    /**
     * El resolutor no compone ninguna ventana de versión propia: Magento ya
     * acota todo `Select` del framework a la versión aplicada, y un segundo
     * filtro anclado en otra ancla es el defecto C2. Ver
     * `Model\VersioningSchema` y `Test/Unit/Model/NoOwnVersionFilterTest`.
     */
    public function testItAddsNoVersionPredicateOfItsOwn(): void
    {
        $selects = [];
        $resolver = $this->makeResolver(hasRowId: true, rows: [
            ['row_id' => 101, 'entity_id' => 1001, 'sku' => 'SKU-ENTIDAD-1001'],
        ], selects: $selects);

        $resolver->resolve([1001]);

        $this->assertNotSame([], $selects, 'la prueba no afirmaría nada sin una consulta');
        foreach ($selects as $select) {
            foreach ($select->wheres as $where) {
                $this->assertStringNotContainsString('created_in', $where['cond']);
                $this->assertStringNotContainsString('updated_in', $where['cond']);
            }
        }
    }

    /**
     * @param list<array<string, mixed>> $rows
     * @param list<SkuResolverFakeSelect> $selects
     */
    private function makeResolver(
        bool $hasRowId,
        array $rows,
        array &$selects = [],
        ?int &$queries = null
    ): SkuResolver {
        $queries = 0;
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => $column === 'row_id' && $hasRowId
        );
        $connection->method('select')->willReturnCallback(
            static function () use (&$selects): SkuResolverFakeSelect {
                $select = new SkuResolverFakeSelect();
                $selects[] = $select;
                return $select;
            }
        );
        $connection->method('fetchAll')->willReturnCallback(
            function (SkuResolverFakeSelect $select) use ($rows, &$queries): array {
                $queries++;
                return $this->evaluate($select, $rows);
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new SkuResolver($resource, new EntityKeyResolver($resource));
    }

    /**
     * @param list<array<string, mixed>> $rows
     * @return list<array<string, mixed>>
     */
    private function evaluate(SkuResolverFakeSelect $select, array $rows): array
    {
        foreach ($select->wheres as $where) {
            if (preg_match('/^e\.(\w+) IN \(\?\)$/', $where['cond'], $m) !== 1) {
                throw new \RuntimeException("condición no reconocida: {$where['cond']}");
            }
            $column = $m[1];
            $wanted = array_map('intval', (array) $where['value']);
            $rows = array_values(array_filter(
                $rows,
                static fn (array $row): bool => in_array((int) ($row[$column] ?? -1), $wanted, true)
            ));
        }

        return $rows;
    }
}

/**
 * Doble mínimo de `Magento\Framework\DB\Select`, del mismo patrón que los de
 * ProductReaderTest y ChecksumReaderTest: registra sólo lo que SkuResolver usa.
 */
final class SkuResolverFakeSelect extends \Magento\Framework\DB\Select
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

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[] = ['cond' => $cond, 'value' => $value];
        return $this;
    }
}
