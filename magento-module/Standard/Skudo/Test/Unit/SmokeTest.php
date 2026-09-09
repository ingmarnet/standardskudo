<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit;

use Magento\Framework\App\ProductMetadataInterface;
use PHPUnit\Framework\TestCase;

class SmokeTest extends TestCase
{
    public function testMagentoFrameworkIsAutoloadable(): void
    {
        $this->assertTrue(
            interface_exists(ProductMetadataInterface::class),
            'el autoloader de la instalación Magento no está disponible'
        );
    }

    public function testModuleNamespaceIsAutoloadable(): void
    {
        $this->assertTrue(
            class_exists(\Standard\Skudo\Test\Unit\SmokeTest::class),
            'el namespace del módulo no es autocargable'
        );
    }
}
