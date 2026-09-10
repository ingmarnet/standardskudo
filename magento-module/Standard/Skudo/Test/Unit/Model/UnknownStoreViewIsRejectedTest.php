<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Exception\InputException;
use Magento\Framework\Exception\NoSuchEntityException;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\Store;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ContentDigest;
use Standard\Skudo\Model\ChecksumReader;
use Standard\Skudo\Model\Cursor;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\ProductReader;
use Standard\Skudo\Model\SignalReader;
use Standard\Skudo\Model\StoreViewGuard;

/**
 * M3, al nivel del endpoint: los CUATRO caminos que reciben un `storeId` por
 * la red lo validan, y ninguno responde 200 para una store view que la
 * instancia no conoce.
 *
 * `StoreViewGuardTest` prueba el guard; esta prueba es la que fallaría si
 * alguien agregara un quinto camino con `storeId` y se olvidara de llamarlo,
 * o quitara la llamada de uno de los cuatro. La lista está acá y no en un
 * comentario justamente por eso.
 *
 * `/checksums` está incluido aunque su `$storeId` no filtre (Ruling 2 de
 * ChecksumReader): un storeId inexistente que devuelve 200 hace que los dos
 * lados de `reconcile()` coincidan sobre un espejo construido con los
 * valores globales de una tienda que no existe.
 */
class UnknownStoreViewIsRejectedTest extends TestCase
{
    /**
     * @return array<string, array{0: string}>
     */
    public static function storeScopedCallProvider(): array
    {
        return [
            'ProductReader::getPage' => ['product_page'],
            'ProductReader::getBySku' => ['product_by_sku'],
            'SignalReader::getSignals' => ['signals'],
            'ChecksumReader::getChecksums' => ['checksums'],
        ];
    }

    /**
     * @dataProvider storeScopedCallProvider
     */
    public function testAnUnknownStoreViewIsRejectedInsteadOfAnsweredWithGlobalValues(string $call): void
    {
        $this->expectException(InputException::class);
        $this->invoke($call, storeId: 999, storeExists: false);
    }

    /**
     * Contra-guarda: con la store view conocida los cuatro responden. Sin
     * esto, un guard que rechazara TODO haría pasar la prueba de arriba y
     * rompería los cuatro endpoints.
     *
     * @dataProvider storeScopedCallProvider
     */
    public function testAKnownStoreViewIsAnswered(string $call): void
    {
        $result = $this->invoke($call, storeId: 1, storeExists: true);

        $this->assertIsArray($result);
        $this->assertCount(1, $result, 'el payload viaja envuelto un nivel');
    }

    /**
     * @return mixed[]
     */
    private function invoke(string $call, int $storeId, bool $storeExists): array
    {
        $resource = $this->resource();
        $guard = $this->guard($storeExists);

        return match ($call) {
            'product_page' => (new ProductReader(
                $resource,
                new Cursor(),
                new EntityKeyResolver($resource),
                $guard
            ))->getPage($storeId, 10),
            'product_by_sku' => (new ProductReader(
                $resource,
                new Cursor(),
                new EntityKeyResolver($resource),
                $guard
            ))->getBySku($storeId, ['SKU-A']),
            'signals' => (new SignalReader(
                $resource,
                $this->modules(),
                new EntityKeyResolver($resource),
                $guard
            ))->getSignals($storeId, 90),
            'checksums' => (new ChecksumReader($resource, new ContentDigest(), $guard))->getChecksums($storeId),
        };
    }

    /**
     * El `getBySku` con una lista de SKUs y el `getPage` tienen que fallar
     * ANTES de consultar nada: si la validación estuviera después de la
     * primera consulta, un storeId inexistente ya habría costado un barrido
     * de catálogo. El doble no devuelve filas para nada, así que la prueba
     * de "known store view" pasa con payloads vacíos, que es suficiente para
     * la contra-guarda.
     */
    private function resource(): ResourceConnection
    {
        $connection = $this->createMock(AdapterInterface::class);
        $select = $this->createMock(\Magento\Framework\DB\Select::class);
        $select->method('from')->willReturnSelf();
        $select->method('join')->willReturnSelf();
        $select->method('where')->willReturnSelf();
        $select->method('order')->willReturnSelf();
        $select->method('group')->willReturnSelf();
        $select->method('limit')->willReturnSelf();

        $connection->method('select')->willReturn($select);
        $connection->method('fetchAll')->willReturn([]);
        $connection->method('fetchCol')->willReturn([]);
        $connection->method('fetchOne')->willReturn('4');
        $connection->method('tableColumnExists')->willReturn(false);
        $connection->method('isTableExists')->willReturn(false);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return $resource;
    }

    private function guard(bool $storeExists): StoreViewGuard
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        if ($storeExists) {
            $storeManager->method('getStore')->willReturn($this->createMock(Store::class));
        } else {
            $storeManager->method('getStore')->willThrowException(new NoSuchEntityException(__('nope')));
        }

        return new StoreViewGuard($storeManager);
    }

    private function modules(): ModuleListInterface
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(false);

        return $modules;
    }
}
