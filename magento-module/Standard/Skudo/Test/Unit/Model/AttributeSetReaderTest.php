<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\DB\Select;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\AttributeSetReader;
use Standard\Skudo\Model\EntityTypeResolver;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;

/**
 * Cubre el endpoint `/attribute-sets`: da el NOMBRE de cada attribute set de
 * `catalog_product`, lo único que el espejo no tenía para identificar un scope
 * `attribute_set:N`.
 *
 * Dos puntos concretos:
 *  1. Sólo los sets de `catalog_product` (el `entity_type_id` se resuelve en
 *     runtime; los sets de otros entity types no viajan).
 *  2. La forma de cada item es `{magento_id: int, name: string}`, con tipos
 *     forzados, y la respuesta va envuelta (`WebApiEnvelope`).
 */
class AttributeSetReaderTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    private const CATALOG_PRODUCT_ENTITY_TYPE_ID = 4;

    public function testDevuelveLosSetsConSuNombreYTiposForzados(): void
    {
        $reader = new AttributeSetReader(
            $this->resourceReturning([
                ['magento_id' => '4', 'name' => 'Default'],
                ['magento_id' => '16', 'name' => 'Calzado'],
            ]),
            $this->entityTypeResolver(),
        );

        $payload = $this->payloadOf($reader->getSets());

        $this->assertSame(
            [
                ['magento_id' => 4, 'name' => 'Default'],
                ['magento_id' => 16, 'name' => 'Calzado'],
            ],
            $payload['items']
        );
        // ids como int, no string: un scope se compara por id.
        $this->assertIsInt($payload['items'][0]['magento_id']);
    }

    public function testCatalogoSinSetsDevuelveListaVaciaEnvuelta(): void
    {
        $reader = new AttributeSetReader(
            $this->resourceReturning([]),
            $this->entityTypeResolver(),
        );

        $payload = $this->payloadOf($reader->getSets());
        $this->assertSame([], $payload['items']);
    }

    private function resourceReturning(array $rows): ResourceConnection
    {
        $select = $this->createMock(Select::class);
        $select->method('from')->willReturnSelf();
        $select->method('where')->willReturnSelf();
        $select->method('order')->willReturnSelf();

        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturn($select);
        $connection->method('fetchAll')->willReturn($rows);
        // entity_type_id de catalog_product, que EntityTypeResolver resuelve.
        $connection->method('fetchOne')->willReturn((string) self::CATALOG_PRODUCT_ENTITY_TYPE_ID);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return $resource;
    }

    private function entityTypeResolver(): EntityTypeResolver
    {
        // Se construye con un resource cuyo fetchOne ya devuelve el id: la
        // resolución real vive en EntityTypeResolver y aquí sólo se satisface.
        $connection = $this->createMock(AdapterInterface::class);
        $select = $this->createMock(Select::class);
        $select->method('from')->willReturnSelf();
        $select->method('where')->willReturnSelf();
        $connection->method('select')->willReturn($select);
        $connection->method('fetchOne')->willReturn((string) self::CATALOG_PRODUCT_ENTITY_TYPE_ID);
        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new EntityTypeResolver($resource);
    }
}
