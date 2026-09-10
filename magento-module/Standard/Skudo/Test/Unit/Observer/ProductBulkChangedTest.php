<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Observer;

use Magento\Framework\Event;
use Magento\Framework\Event\Observer;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;
use Standard\Skudo\Model\ChangeLog;
use Standard\Skudo\Model\SkuResolution;
use Standard\Skudo\Model\SkuResolver;
use Standard\Skudo\Observer\ProductBulkChanged;

/**
 * H1: los dos caminos de escritura masiva de Magento no despachan
 * `catalog_product_save_after` y por lo tanto no dejaban rastro en la cola de
 * cambios. Estas pruebas afirman que ahora sí, y —lo que más importa— que el
 * observer nunca se queda callado cuando NO registra: un observer que anota
 * cero filas sin decirlo simula cobertura, que es peor que no tenerlo.
 */
class ProductBulkChangedTest extends TestCase
{
    /**
     * `Product\Action::updateAttributes()` despacha
     * `catalog_product_attribute_update_before` con `product_ids`.
     */
    public function testTheMassAttributeUpdateEventRecordsEveryResolvedSku(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('recordMany')
            ->with(['SKU-A', 'SKU-B'], 'save');

        $this->makeObserver($log, new SkuResolution(['SKU-A', 'SKU-B'], []))
            ->execute($this->eventWith(
                'catalog_product_attribute_update_before',
                ['product_ids' => [1001, 1002], 'store_id' => 0]
            ));
    }

    /**
     * `Product\Action::updateWebsites()` despacha
     * `catalog_product_to_website_change` con `products`, y es el camino
     * donde `updated_at` NO se mueve: sin este observer, ni la cola ni el
     * digest de contenido lo verían nunca.
     */
    public function testTheWebsiteChangeEventRecordsEveryResolvedSku(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('recordMany')
            ->with(['SKU-DELTA'], 'save');

        $this->makeObserver($log, new SkuResolution(['SKU-DELTA'], []))
            ->execute($this->eventWith(
                'catalog_product_to_website_change',
                ['products' => [1004]]
            ));
    }

    public function testTheIdsOfTheEventAreTheOnesHandedToTheResolver(): void
    {
        $seen = null;
        $resolver = $this->createMock(SkuResolver::class);
        $resolver->method('resolve')->willReturnCallback(
            function (array $ids) use (&$seen): SkuResolution {
                $seen = $ids;
                return new SkuResolution(['SKU-A'], []);
            }
        );

        $observer = new ProductBulkChanged(
            $this->createMock(ChangeLog::class),
            $resolver,
            $this->createMock(LoggerInterface::class)
        );
        $observer->execute($this->eventWith(
            'catalog_product_attribute_update_before',
            ['product_ids' => ['1001', 1002]]
        ));

        $this->assertSame([1001, 1002], $seen);
    }

    /**
     * La guarda central: un id que no resuelve es un cambio PERDIDO. Tiene
     * que quedar en el log del cliente, nombrando el id, o nadie sabrá nunca
     * por qué ese producto quedó rancio.
     */
    public function testAnUnresolvedIdIsLoggedAndTheRestStillRecorded(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('recordMany')->with(['SKU-A'], 'save');

        $logger = $this->createMock(LoggerInterface::class);
        $logger->expects($this->once())->method('warning')
            ->with($this->logicalAnd(
                $this->stringContains('4242'),
                $this->stringContains('standard_skudo_change_log')
            ));

        $observer = new ProductBulkChanged(
            $log,
            $this->resolverReturning(new SkuResolution(['SKU-A'], [4242])),
            $logger
        );
        $observer->execute($this->eventWith(
            'catalog_product_attribute_update_before',
            ['product_ids' => [1001, 4242]]
        ));
    }

    /**
     * Si Magento renombrara la clave de la carga, el observer registraría
     * cero. Que eso pase en silencio es el defecto H1 otra vez, con otra
     * causa; así que se exige la línea de log.
     */
    public function testAnEventWithoutAKnownIdPayloadIsLoggedInsteadOfIgnored(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->never())->method('recordMany');

        $logger = $this->createMock(LoggerInterface::class);
        $logger->expects($this->once())->method('warning')
            ->with($this->stringContains('sin ninguna de las claves de ids'));

        $observer = new ProductBulkChanged(
            $log,
            $this->resolverReturning(new SkuResolution([], [])),
            $logger
        );
        $observer->execute($this->eventWith(
            'catalog_product_attribute_update_before',
            ['algo_completamente_distinto' => [1]]
        ));
    }

    /**
     * Una selección vacía sí es un no-evento: no hubo escritura. No se
     * registra y NO se avisa, porque un aviso por cada no-evento entrena a
     * quien lee el log a ignorarlo.
     */
    public function testAnEmptySelectionRecordsNothingAndDoesNotWarn(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->never())->method('recordMany');

        $logger = $this->createMock(LoggerInterface::class);
        $logger->expects($this->never())->method('warning');

        $observer = new ProductBulkChanged(
            $log,
            $this->resolverReturning(new SkuResolution([], [])),
            $logger
        );
        $observer->execute($this->eventWith(
            'catalog_product_to_website_change',
            ['products' => []]
        ));
    }

    private function makeObserver(ChangeLog $log, SkuResolution $resolution): ProductBulkChanged
    {
        return new ProductBulkChanged(
            $log,
            $this->resolverReturning($resolution),
            $this->createMock(LoggerInterface::class)
        );
    }

    private function resolverReturning(SkuResolution $resolution): SkuResolver
    {
        $resolver = $this->createMock(SkuResolver::class);
        $resolver->method('resolve')->willReturn($resolution);
        return $resolver;
    }

    /**
     * @param array<string, mixed> $data
     */
    private function eventWith(string $name, array $data): Observer
    {
        $event = $this->createMock(Event::class);
        $event->method('getName')->willReturn($name);
        $event->method('getData')->willReturnCallback(
            static fn (string $key) => $data[$key] ?? null
        );

        $observer = $this->createMock(Observer::class);
        $observer->method('getEvent')->willReturn($event);

        return $observer;
    }
}
