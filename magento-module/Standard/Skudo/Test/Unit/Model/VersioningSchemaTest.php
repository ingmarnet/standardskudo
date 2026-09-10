<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\VersioningSchema;

/**
 * Lo que queda de `ActiveVersionResolverTest` tras C2: la detección de las
 * columnas de versionado, y nada más. Las pruebas de `applyToSelect()`
 * desaparecieron con el método — afirmaban que el módulo agregaba su propia
 * ventana de versión activa, que es justo el defecto. Ver
 * `Model\VersioningSchema` y `NoOwnVersionFilterTest`.
 *
 * La detección sigue haciendo falta para un solo caller real,
 * `DeltaReader::activatedVersions()`, cuya consulta nombra `created_in`
 * explícitamente: en una instalación sin versionado esa columna no existe y
 * la consulta sería un error de SQL.
 */
class VersioningSchemaTest extends TestCase
{
    public function testIsVersionedIsTrueWhenColumnsArePresent(): void
    {
        $this->assertTrue($this->schema(isVersioned: true)->isVersioned());
    }

    public function testIsVersionedIsFalseWhenColumnsAreAbsent(): void
    {
        $this->assertFalse($this->schema(isVersioned: false)->isVersioned());
    }

    /**
     * La detección se hace por columnas y NUNCA por edición ni por lista de
     * módulos: `tableColumnExists` es lo único que se consulta. Si alguien
     * la cambiara por `ProductMetadata::getEdition()`, esta prueba —que no
     * le da ninguna otra fuente de verdad— seguiría siendo la que define el
     * contrato.
     */
    public function testIsVersionedRequiresBothColumnsNotJustOne(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => $column === 'created_in'
        );

        $this->assertFalse((new VersioningSchema($this->resourceFor($connection)))->isVersioned());
    }

    /**
     * Memoización: llamar isVersioned() varias veces no debe repetir la
     * prueba de esquema. `tableColumnExists()` se invoca dos veces en total
     * (created_in y updated_in), nunca más, sin importar cuántas veces se
     * llame desde afuera — un endpoint de deltas se llama en cada ciclo de
     * polling.
     */
    public function testIsVersionedIsMemoizedAndProbesTheSchemaOnlyOnce(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->exactly(2))
            ->method('tableColumnExists')
            ->with('catalog_product_entity', $this->logicalOr('created_in', 'updated_in'))
            ->willReturn(true);

        $schema = new VersioningSchema($this->resourceFor($connection));

        $this->assertTrue($schema->isVersioned());
        $this->assertTrue($schema->isVersioned());
        $this->assertTrue($schema->isVersioned());
    }

    /**
     * La memoización es POR TABLA, no global: `catalog_category_entity`
     * también es staged, y una respuesta cacheada para una tabla no debe
     * contestar por la otra.
     */
    public function testMemoizationIsPerTableNotGlobal(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->exactly(3))
            ->method('tableColumnExists')
            ->willReturnCallback(
                static fn (string $table, string $column): bool => $table === 'catalog_product_entity'
            );

        $schema = new VersioningSchema($this->resourceFor($connection));

        $this->assertTrue($schema->isVersioned('catalog_product_entity'));
        $this->assertFalse($schema->isVersioned('catalog_category_entity'));
        $this->assertTrue($schema->isVersioned('catalog_product_entity'));
    }

    private function schema(bool $isVersioned): VersioningSchema
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturn($isVersioned);

        return new VersioningSchema($this->resourceFor($connection));
    }

    private function resourceFor(AdapterInterface $connection): ResourceConnection
    {
        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return $resource;
    }
}
