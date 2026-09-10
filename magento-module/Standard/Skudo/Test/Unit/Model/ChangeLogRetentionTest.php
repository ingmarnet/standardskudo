<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ChangeLogRetention;
use Standard\Skudo\Model\DeltaReadWatermark;

/**
 * M7. La poda de `standard_skudo_change_log`, que corre contra la base de
 * PRODUCCIÓN del cliente y borra filas.
 *
 * El fallo que estas pruebas existen para impedir no es "no podar": es podar
 * una fila que el ingestor no leyó. Ese cambio no lo reemite nadie —la cola es
 * la única señal de una escritura masiva o de un `updateWebsites()`, ver H1— y
 * la reconciliación sólo lo detecta si movió `updated_at`. Así que las dos
 * condiciones del borrado se afirman por separado, y la primera se afirma
 * también en su caso extremo: sin constancia de consumo, ni un DELETE.
 */
class ChangeLogRetentionTest extends TestCase
{
    public function testWithoutEvidenceOfConsumptionNothingIsDeletedAtAll(): void
    {
        $queries = [];
        $retention = $this->makeRetention($queries, consumed: 0, counted: 4321, deleted: 4321);

        $result = $retention->prune(30);

        $this->assertSame(0, $result['deleted']);
        $this->assertSame(0, $result['deletable']);
        $this->assertSame(
            [],
            $this->deleteStatements($queries),
            'no debe emitirse NINGÚN DELETE: con el watermark en 0 no hay '
            . 'constancia de que el ingestor haya consumido una sola fila'
        );
    }

    /**
     * Las DOS condiciones, afirmadas por separado. Si se cayera la del
     * `change_id`, la poda borraría por encima de lo consumido; si se cayera
     * la de la fecha, desaparecería el margen que sobrevive a un watermark
     * equivocado.
     */
    public function testTheDeleteRequiresBothConsumptionAndTheSafetyMargin(): void
    {
        $queries = [];
        $retention = $this->makeRetention($queries, consumed: 900, counted: 7, deleted: 7);

        $result = $retention->prune(30);

        $deletes = $this->deleteStatements($queries);
        $this->assertCount(1, $deletes);
        $this->assertStringContainsString('change_id <= ?', $deletes[0]['sql']);
        $this->assertStringContainsString(
            'changed_at < DATE_SUB(NOW(), INTERVAL 30 DAY)',
            $deletes[0]['sql']
        );
        $this->assertSame([900], $deletes[0]['bind']);
        $this->assertSame(7, $result['deleted']);
    }

    /**
     * El corte se calcula en SQL y no en PHP: `changed_at` lo escribe MySQL
     * con su reloj y su zona, y una cadena compuesta en PHP se desplaza en
     * silencio en cuanto los dos relojes o las dos zonas difieren — borrando
     * de más o de menos sin que nada lo diga.
     */
    public function testTheCutoffIsComputedBySqlAndNotByPhp(): void
    {
        $queries = [];
        $this->makeRetention($queries, consumed: 900, counted: 0, deleted: 0)->prune(7);

        $deletes = $this->deleteStatements($queries);
        $this->assertStringContainsString('DATE_SUB(NOW()', $deletes[0]['sql']);
        $this->assertStringNotContainsString(
            (new \DateTimeImmutable('-7 days'))->format('Y-m-d'),
            $deletes[0]['sql'],
            'el corte no puede venir de una fecha compuesta en PHP'
        );
    }

    /**
     * El ensayo cuenta con el MISMO predicado que usaría el borrado. Un
     * `--dry-run` que informara un conjunto distinto del que el borrado toca
     * sería peor que no tener ensayo: daría permiso para borrar otra cosa.
     */
    public function testTheDryRunCountsExactlyWhatTheDeleteWouldRemove(): void
    {
        $ensayo = [];
        $this->makeRetention($ensayo, consumed: 900, counted: 5, deleted: 5)
            ->prune(30, dryRun: true);
        $borrado = [];
        $this->makeRetention($borrado, consumed: 900, counted: 5, deleted: 5)->prune(30);

        $this->assertSame([], $this->deleteStatements($ensayo), 'el ensayo no borra');
        $this->assertSame(
            $this->whereOf($this->countStatements($ensayo)[0]['sql']),
            $this->whereOf($this->deleteStatements($borrado)[0]['sql'])
        );
    }

    public function testTheDryRunReportsTheCountItWouldDelete(): void
    {
        $queries = [];
        $result = $this->makeRetention($queries, consumed: 900, counted: 5, deleted: 99)
            ->prune(30, dryRun: true);

        $this->assertSame(5, $result['deletable']);
        $this->assertSame(0, $result['deleted']);
        $this->assertTrue($result['dry_run']);
    }

    /**
     * Un margen de 0 días dejaría la condición del watermark como única red.
     * Se rechaza en vez de obedecer: el que pasa 0 casi siempre quiere decir
     * "borrá todo lo consumido" sin haber pensado en qué pasa si el watermark
     * está mal.
     */
    public function testAMarginBelowTheFloorIsRefused(): void
    {
        $queries = [];
        $retention = $this->makeRetention($queries, consumed: 900, counted: 5, deleted: 5);

        $this->expectException(\InvalidArgumentException::class);

        $retention->prune(0);
    }

    /** @param list<array{sql: string, bind: mixed}> $queries */
    private function deleteStatements(array $queries): array
    {
        return array_values(array_filter(
            $queries,
            static fn (array $query): bool => str_starts_with($query['sql'], 'DELETE')
        ));
    }

    /** @param list<array{sql: string, bind: mixed}> $queries */
    private function countStatements(array $queries): array
    {
        return array_values(array_filter(
            $queries,
            static fn (array $query): bool => str_contains($query['sql'], 'COUNT(*)')
                && str_contains($query['sql'], 'WHERE')
        ));
    }

    private function whereOf(string $sql): string
    {
        $position = strpos($sql, 'WHERE ');
        return $position === false ? '' : substr($sql, $position);
    }

    /** @param list<array{sql: string, bind: mixed}> $queries */
    private function makeRetention(
        array &$queries,
        int $consumed,
        int $counted,
        int $deleted
    ): ChangeLogRetention {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('fetchOne')->willReturnCallback(
            function (mixed $sql, mixed $bind = []) use (&$queries, $counted): mixed {
                $queries[] = ['sql' => (string) $sql, 'bind' => $bind];
                // El COUNT sin WHERE es el tamaño de la cola; con WHERE, el
                // conjunto podable.
                return str_contains((string) $sql, 'WHERE') ? $counted : 1000;
            }
        );
        $connection->method('query')->willReturnCallback(
            function (mixed $sql, mixed $bind = []) use (&$queries, $deleted) {
                $queries[] = ['sql' => (string) $sql, 'bind' => $bind];
                $statement = $this->createMock(\Zend_Db_Statement_Interface::class);
                $statement->method('rowCount')->willReturn($deleted);
                return $statement;
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $watermark = $this->createMock(DeltaReadWatermark::class);
        $watermark->method('consumed')->willReturn($consumed);
        $watermark->method('observedAt')->willReturn('2026-09-10 03:27:00');

        return new ChangeLogRetention($resource, $watermark);
    }
}
