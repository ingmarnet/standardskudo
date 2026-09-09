<?php
declare(strict_types=1);

namespace Standard\Skudo\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Standard\Skudo\Model\ChangeLog;

/**
 * Alimenta la cola de cambios desde los eventos de guardado/borrado de
 * producto. Cubre ediciones y borrados; NO cubre la activación de una
 * versión programada (Magento_Staging) porque ese evento no existe: ver
 * el comentario en DeltaReader::getChanges() sobre por qué ese caso se
 * resuelve ahí y no con un observer.
 */
class ProductChanged implements ObserverInterface
{
    public function __construct(private readonly ChangeLog $log)
    {
    }

    public function execute(Observer $observer): void
    {
        $product = $observer->getEvent()->getData('product');
        $sku = $product?->getSku();

        if ($sku === null || $sku === '') {
            return;
        }

        $event = str_contains($observer->getEvent()->getName(), 'delete') ? 'delete' : 'save';
        $this->log->record((string) $sku, $event);
    }
}
