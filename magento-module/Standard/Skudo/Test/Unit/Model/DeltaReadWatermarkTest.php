<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\DeltaReadWatermark;

/**
 * M7. Este contador es lo único que hace SEGURA la poda de la cola: la poda no
 * borra por encima de él, así que si se moviera de más borraría cambios que el
 * ingestor no leyó — y esos cambios no los reemite nadie (no hay evento que
 * los repita, y la reconciliación sólo los detecta si movieron `updated_at`).
 *
 * Las tres propiedades que estas pruebas vigilan son exactamente las tres que,
 * si se rompen, hacen que la poda borre lo que no debe o deje de podar para
 * siempre.
 *
 * Sobre la forma de las aserciones: acá NO hay una base de datos que ejecute
 * el `ON DUPLICATE KEY UPDATE`, y la semántica de "no retroceder" vive en el
 * `GREATEST` de ese SQL. Así que se afirma sobre la sentencia y sus binds, que
 * es lo único observable sin una instancia. La comprobación con MySQL de
 * verdad está en el informe de H3 (§ verificación sobre la instancia de
 * desarrollo), ejecutada contra `skudo_dev_db`.
 */
class DeltaReadWatermarkTest extends TestCase
{
    public function testRecordingNeverLetsTheWatermarkGoBackwards(): void
    {
        $queries = [];
        $watermark = $this->makeWatermark($queries, consumed: 500);

        $watermark->record(120);

        $this->assertCount(1, $queries);
        $this->assertStringContainsString('INSERT INTO', $queries[0]['sql']);
        $this->assertStringContainsString(
            'GREATEST(consumed_change_id, ?)',
            $queries[0]['sql'],
            'sin GREATEST, una petición con un sinceId más bajo —un reintento, '
            . 'un consumidor recién inicializado— haría RETROCEDER el watermark '
            . 'y la poda dejaría de borrar'
        );
        $this->assertSame([120, 120], $queries[0]['bind']);
    }

    /**
     * Un consumidor que pide `sinceId=0` está pidiendo la cola DESDE EL
     * PRINCIPIO: no ha consumido nada. Escribir una fila por esa petición
     * afirmaría lo contrario, y con un 0 la poda igual no borraría nada, así
     * que la fila sólo serviría para que `observedAt()` mintiera sobre que
     * alguien leyó.
     */
    public function testAConsumerThatHasConsumedNothingWritesNothing(): void
    {
        $queries = [];
        $watermark = $this->makeWatermark($queries, consumed: 0);

        $watermark->record(0);

        $this->assertSame([], $queries);
    }

    /**
     * La contabilidad de la poda no puede tumbar la sincronización del
     * cliente. El efecto de tragarse el error es que el watermark se queda
     * atrás y la poda borra MENOS: la dirección segura.
     */
    public function testAFailedWriteDoesNotBreakTheRead(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('query')->willThrowException(new \RuntimeException('disco lleno'));

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        (new DeltaReadWatermark($resource))->record(42);

        $this->addToAssertionCount(1);
    }

    /**
     * Sin fila, `fetchOne` devuelve false y el contador es 0 — que significa
     * "no se sabe que se haya consumido nada" y hace que la poda no borre ni
     * una fila. Un cast descuidado que devolviera otra cosa (o que reventara)
     * dejaría a la poda sin su condición principal.
     */
    public function testWithNoRowTheConsumedIdIsZero(): void
    {
        $queries = [];
        $watermark = $this->makeWatermark($queries, consumed: false);

        $this->assertSame(0, $watermark->consumed());
        $this->assertNull($watermark->observedAt());
    }

    /**
     * @param list<array{sql: string, bind: mixed}> $queries
     */
    private function makeWatermark(array &$queries, mixed $consumed): DeltaReadWatermark
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('query')->willReturnCallback(
            function (mixed $sql, mixed $bind = []) use (&$queries) {
                $queries[] = ['sql' => (string) $sql, 'bind' => $bind];
                return $this->createMock(\Zend_Db_Statement_Interface::class);
            }
        );
        $connection->method('select')->willReturnCallback(
            static fn (): WatermarkFakeSelect => new WatermarkFakeSelect()
        );
        $connection->method('fetchOne')->willReturn($consumed);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new DeltaReadWatermark($resource);
    }
}

/** Doble mínimo de `Select`, como los de ProductReaderTest y DeltaReaderTest. */
final class WatermarkFakeSelect extends \Magento\Framework\DB\Select
{
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
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        return $this;
    }
}
