<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\EntityKeyResolver;

class EntityKeyResolverTest extends TestCase
{
    private function resolver(bool $hasRowId): EntityKeyResolver
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')
            ->with('catalog_product_entity', 'row_id')
            ->willReturn($hasRowId);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new EntityKeyResolver($resource);
    }

    public function testDetectsRowIdFromSchemaWhenColumnExists(): void
    {
        $resolver = $this->resolver(true);
        $this->assertSame('row_id', $resolver->resolve());
    }

    public function testFallsBackToEntityIdWhenRowIdColumnIsAbsent(): void
    {
        $resolver = $this->resolver(false);
        $this->assertSame('entity_id', $resolver->resolve());
    }

    public function testResolutionIsMemoizedAndProbesTheSchemaOnlyOnce(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->once())
            ->method('tableColumnExists')
            ->with('catalog_product_entity', 'row_id')
            ->willReturn(true);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new EntityKeyResolver($resource);

        // Paginar cientos de miles de productos llama resolve() en cada
        // página: si esto no memoizara, cada página repetiría la consulta
        // de esquema.
        $this->assertSame('row_id', $resolver->resolve());
        $this->assertSame('row_id', $resolver->resolve());
        $this->assertSame('row_id', $resolver->resolve());
    }
}
