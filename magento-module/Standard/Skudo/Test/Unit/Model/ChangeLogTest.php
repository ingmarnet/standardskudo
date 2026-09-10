<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ChangeLog;

/**
 * H1: la cola tenía un solo camino de escritura, `record()`, una fila por
 * llamada. Los eventos de escritura MASIVA llegan con miles de SKUs de una
 * vez —una acción del grid de admin sobre una selección entera, o una carga
 * por lotes—, y una fila por `INSERT` son miles de idas y vueltas dentro de
 * la petición del admin: el observer se vuelve el cuello de botella de la
 * operación que observa, y un observer caro es un observer que alguien
 * desactiva.
 */
class ChangeLogTest extends TestCase
{
    public function testASingleSkuIsInsertedAsBefore(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->once())->method('insert')
            ->with('standard_skudo_change_log', ['sku' => 'SKU-A', 'event' => 'save']);

        $this->makeLog($connection)->record('SKU-A', 'save');
    }

    public function testManySkusGoInOneMultiRowInsert(): void
    {
        $calls = [];
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->never())->method('insert');
        $connection->method('insertMultiple')->willReturnCallback(
            function (string $table, array $rows) use (&$calls): int {
                $calls[] = $rows;
                return count($rows);
            }
        );

        $this->makeLog($connection)->recordMany(['SKU-A', 'SKU-B', 'SKU-C'], 'save');

        $this->assertCount(1, $calls);
        $this->assertSame([
            ['sku' => 'SKU-A', 'event' => 'save'],
            ['sku' => 'SKU-B', 'event' => 'save'],
            ['sku' => 'SKU-C', 'event' => 'save'],
        ], $calls[0]);
    }

    /**
     * Un lote más grande que el tope se parte: un `INSERT` de 228.881 filas
     * es un paquete que MySQL rechaza por `max_allowed_packet`, y ese rechazo
     * llegaría como una excepción DENTRO de la acción de admin del cliente.
     */
    public function testALotBiggerThanTheChunkIsSplit(): void
    {
        $sizes = [];
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('insertMultiple')->willReturnCallback(
            function (string $table, array $rows) use (&$sizes): int {
                $sizes[] = count($rows);
                return count($rows);
            }
        );

        $skus = [];
        for ($i = 0; $i < ChangeLog::CHUNK + 3; $i++) {
            $skus[] = 'SKU-' . $i;
        }

        $this->makeLog($connection)->recordMany($skus, 'save');

        $this->assertSame([ChangeLog::CHUNK, 3], $sizes);
    }

    /**
     * Una lista vacía no escribe: un `insertMultiple` con cero filas es un
     * SQL inválido, y "no había nada que registrar" no es un error.
     */
    public function testAnEmptyLotWritesNothing(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->never())->method('insertMultiple');
        $connection->expects($this->never())->method('insert');

        $this->makeLog($connection)->recordMany([], 'save');
    }

    private function makeLog(AdapterInterface $connection): ChangeLog
    {
        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new ChangeLog($resource);
    }
}
