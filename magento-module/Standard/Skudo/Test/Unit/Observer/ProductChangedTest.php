<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Observer;

use Magento\Framework\Event;
use Magento\Framework\Event\Observer;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ChangeLog;
use Standard\Skudo\Observer\ProductChanged;

class ProductChangedTest extends TestCase
{
    public function testSaveIsRecordedAsSave(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('record')->with('SKU1', 'save');

        (new ProductChanged($log))->execute($this->observerFor('SKU1', 'catalog_product_save_after'));
    }

    public function testDeleteIsRecordedAsDelete(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('record')->with('SKU1', 'delete');

        (new ProductChanged($log))->execute(
            $this->observerFor('SKU1', 'catalog_product_delete_after')
        );
    }

    public function testProductWithoutSkuIsIgnored(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->never())->method('record');

        (new ProductChanged($log))->execute($this->observerFor(null, 'catalog_product_save_after'));
    }

    private function observerFor(?string $sku, string $eventName): Observer
    {
        $product = new class ($sku) {
            public function __construct(private readonly ?string $sku)
            {
            }

            public function getSku(): ?string
            {
                return $this->sku;
            }
        };

        $event = $this->createMock(Event::class);
        $event->method('getName')->willReturn($eventName);
        $event->method('getData')->with('product')->willReturn($product);

        $observer = $this->createMock(Observer::class);
        $observer->method('getEvent')->willReturn($event);

        return $observer;
    }
}
