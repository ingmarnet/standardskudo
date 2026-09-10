<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\DB\Select;
use Magento\Framework\Exception\LocalizedException;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\EntityTypeResolver;

/**
 * La tercera pregunta de esquema, extraída al arreglar M1: traducir un
 * `entity_type_code` a su `entity_type_id`.
 *
 * Estaba escrita dos veces (AttributeReader para `catalog_product`,
 * CategoryReader para `catalog_category`) y M1 pedía una tercera en
 * EnvironmentProbe, que es cuando la duplicación deja de ser tolerable: el
 * día que la resolución cambie y se cambie en dos de los tres lugares, los
 * conteos y las páginas hablarían de tipos de entidad distintos.
 */
class EntityTypeResolverTest extends TestCase
{
    public function testResolvesTheIdFromTheTableAndNeverAssumesIt(): void
    {
        // 17, no 4: el id "típico" de catalog_product es 4, y un resolver que
        // lo asumiera pasaría cualquier prueba que use 4.
        $resolver = $this->resolver(['catalog_product' => '17']);

        $this->assertSame(17, $resolver->resolve(EntityTypeResolver::PRODUCT));
    }

    public function testResolvesEachCodeSeparately(): void
    {
        $resolver = $this->resolver(['catalog_product' => '17', 'catalog_category' => '9']);

        $this->assertSame(17, $resolver->resolve(EntityTypeResolver::PRODUCT));
        $this->assertSame(9, $resolver->resolve(EntityTypeResolver::CATEGORY));
    }

    /**
     * Un código ausente LANZA y no devuelve null ni 0: un `entity_type_id`
     * que no se pudo resolver haría que toda consulta que lo use devuelva
     * las filas de otro tipo de entidad (con 0, ninguna), y las dos son
     * peores que un fallo ruidoso.
     */
    public function testAnAbsentEntityTypeCodeThrows(): void
    {
        $resolver = $this->resolver([]);

        $this->expectException(LocalizedException::class);
        $resolver->resolve('catalog_product');
    }

    public function testTheAnswerIsMemoizedPerCode(): void
    {
        $calls = 0;
        $resolver = $this->resolver(['catalog_product' => '17', 'catalog_category' => '9'], $calls);

        $resolver->resolve(EntityTypeResolver::PRODUCT);
        $resolver->resolve(EntityTypeResolver::PRODUCT);
        $resolver->resolve(EntityTypeResolver::CATEGORY);
        $resolver->resolve(EntityTypeResolver::PRODUCT);

        $this->assertSame(2, $calls, 'una consulta por código, no una por llamada');
    }

    /**
     * @param array<string, string> $idsByCode
     */
    private function resolver(array $idsByCode, int &$calls = 0): EntityTypeResolver
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(
            static fn (): EntityTypeFakeSelect => new EntityTypeFakeSelect()
        );
        $connection->method('fetchOne')->willReturnCallback(
            static function (EntityTypeFakeSelect $select) use ($idsByCode, &$calls) {
                $calls++;
                return $idsByCode[$select->code] ?? false;
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new EntityTypeResolver($resource);
    }
}

/**
 * Doble de `Select` que recuerda el `entity_type_code` pedido: es lo único
 * que decide qué fila devuelve `fetchOne()` en estas pruebas.
 */
final class EntityTypeFakeSelect extends Select
{
    public string $code = '';

    public function __construct()
    {
    }

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        if ($cond === 'entity_type_code = ?') {
            $this->code = (string) $value;
        }
        return $this;
    }
}
