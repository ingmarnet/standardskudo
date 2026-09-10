<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\Exception\InputException;
use Magento\Framework\Exception\LocalizedException;
use Magento\Framework\Exception\NoSuchEntityException;
use Magento\Store\Model\Store;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\StoreViewGuard;

/**
 * M3: un `storeId` inexistente devolvía 200.
 *
 * Verificado sobre HTTP real: `/products?storeId=999` → 200 con items y
 * `store_values: {}` en todos; `/signals?storeId=999` → 200 con
 * `{"items":[]}`. Un storeId mal escrito en la configuración de un tenant
 * hacía que el ingestor espejara los valores GLOBALES como si fueran la
 * verdad de esa tienda, sin un solo error, y como `/checksums` tampoco
 * filtra por store, `reconcile()` diría "sin deriva".
 */
class StoreViewGuardTest extends TestCase
{
    public function testAKnownStoreViewPasses(): void
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStore')->with(1)->willReturn($this->createMock(Store::class));

        $this->expectNotToPerformAssertions();
        (new StoreViewGuard($storeManager))->assertExists(1);
    }

    /**
     * Se afirma `InputException` Y `LocalizedException`: lo primero fija la
     * clase, lo segundo fija la PROPIEDAD de la que depende el 400 (el
     * `ErrorProcessor` de web API mira si es localizada). Y se afirma que NO
     * es `NoSuchEntityException`, que Magento mapearía a 404: el storeId es
     * un parámetro de la petición, no el recurso que la ruta nombra, y un
     * cliente necesita el 400 para no reintentar.
     */
    public function testAnUnknownStoreViewIsRejectedAsClientInput(): void
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStore')->willThrowException(new NoSuchEntityException(__('nope')));

        try {
            (new StoreViewGuard($storeManager))->assertExists(999);
            $this->fail('una store view inexistente tiene que lanzar');
        } catch (\Throwable $e) {
            $this->assertInstanceOf(InputException::class, $e);
            $this->assertInstanceOf(LocalizedException::class, $e);
            $this->assertNotInstanceOf(NoSuchEntityException::class, $e);
            $this->assertStringContainsString('999', $e->getMessage());
        }
    }

    /**
     * Memoización: `full_sync` pagina cientos de miles de productos pidiendo
     * siempre el mismo storeId. `getStore()` se llama una sola vez, sin
     * importar cuántas veces se pregunte.
     */
    public function testTheAnswerIsMemoizedPerStoreId(): void
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->expects($this->once())
            ->method('getStore')
            ->with(1)
            ->willReturn($this->createMock(Store::class));

        $guard = new StoreViewGuard($storeManager);
        $guard->assertExists(1);
        $guard->assertExists(1);
        $guard->assertExists(1);
    }

    /**
     * La memoización del caso NEGATIVO también: un storeId inexistente sigue
     * lanzando en la segunda llamada, no pasa por haber quedado cacheado
     * como "ya preguntado". Sin esta prueba, un `isset()` mal escrito sobre
     * un valor `false` dejaría pasar el segundo intento.
     */
    public function testAnUnknownStoreIdKeepsThrowingOnEveryCall(): void
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->expects($this->once())
            ->method('getStore')
            ->willThrowException(new NoSuchEntityException(__('nope')));

        $guard = new StoreViewGuard($storeManager);

        foreach ([1, 2] as $_) {
            try {
                $guard->assertExists(999);
                $this->fail('tiene que lanzar en cada llamada');
            } catch (InputException) {
                // esperado
            }
        }
    }
}
