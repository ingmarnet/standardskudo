<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Store\Model\StoreManagerInterface;
use Standard\Skudo\Model\StoreViewGuard;

/**
 * StoreViewGuard con un StoreManager que conoce cualquier store view: M3 no
 * es el objeto de prueba de estos archivos, y un guard que rechazara todo
 * haría fallar cada caso por la razón equivocada. El guard de verdad se
 * prueba en StoreViewGuardTest.
 */
trait BuildsAPermissiveStoreViewGuard
{
    private function permissiveStoreViewGuard(): StoreViewGuard
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStore')->willReturn($this->createMock(\Magento\Store\Model\Store::class));

        return new StoreViewGuard($storeManager);
    }
}
