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

    /**
     * S0 Task A3: `catalog_category_entity` es staged igual que
     * `catalog_product_entity` (tiene su propio `row_id`), así que
     * CategoryReader reutiliza esta MISMA clase pasándole su nombre de
     * tabla, en vez de una copia. resolve() debe aceptar la tabla como
     * parámetro y probar el ESQUEMA DE ESA TABLA, no asumir siempre
     * `catalog_product_entity`.
     */
    public function testResolvesForAnArbitraryTableNotJustCatalogProductEntity(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')
            ->with('catalog_category_entity', 'row_id')
            ->willReturn(true);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new EntityKeyResolver($resource);

        $this->assertSame('row_id', $resolver->resolve('catalog_category_entity'));
    }

    /**
     * Dos tablas distintas deben memoizarse por separado: si la instancia
     * probó `catalog_product_entity` (row_id) primero, una llamada
     * posterior para `catalog_category_entity` (entity_id, sin Staging en
     * este caso hipotético) no debe devolver el resultado cacheado de la
     * primera tabla.
     */
    public function testMemoizationIsPerTableNotGlobal(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => $table === 'catalog_product_entity'
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new EntityKeyResolver($resource);

        $this->assertSame('row_id', $resolver->resolve('catalog_product_entity'));
        $this->assertSame('entity_id', $resolver->resolve('catalog_category_entity'));
        // Repetir en el otro orden para confirmar que ninguna sobreescribió a la otra.
        $this->assertSame('row_id', $resolver->resolve('catalog_product_entity'));
    }
}
