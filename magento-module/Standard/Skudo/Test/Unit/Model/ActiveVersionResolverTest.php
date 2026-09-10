<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ActiveVersionResolver;

/**
 * Fix de revisión (ronda 1, Finding 2): ProductReader (Task 8) y DeltaReader
 * (Task 10) reimplementaban cada uno su propia detección memoizada de
 * created_in/updated_in y su propia copia de los dos where() de la ventana
 * de versión activa. Esta clase es el único lugar que responde esa pregunta
 * de esquema y codifica esa ventana; estas pruebas cubren lo mismo que ya
 * cubría EntityKeyResolverTest para la otra pregunta de esquema (row_id vs
 * entity_id): detección real, memoización, y aplicación real del filtro.
 */
class ActiveVersionResolverTest extends TestCase
{
    public function testHasVersioningIsTrueWhenColumnsArePresent(): void
    {
        $resolver = $this->resolver(hasVersioning: true);
        $this->assertTrue($resolver->hasVersioning());
    }

    public function testHasVersioningIsFalseWhenColumnsAreAbsent(): void
    {
        $resolver = $this->resolver(hasVersioning: false);
        $this->assertFalse($resolver->hasVersioning());
    }

    /**
     * Memoización: llamar hasVersioning() varias veces no debe repetir la
     * prueba de esquema. tableColumnExists() se invoca dos veces en total
     * (created_in y updated_in), nunca más, sin importar cuántas veces se
     * llame hasVersioning() desde afuera.
     */
    public function testHasVersioningIsMemoizedAndProbesTheSchemaOnlyOnce(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->expects($this->exactly(2))
            ->method('tableColumnExists')
            ->with('catalog_product_entity', $this->logicalOr('created_in', 'updated_in'))
            ->willReturn(true);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new ActiveVersionResolver($resource);

        $this->assertTrue($resolver->hasVersioning());
        $this->assertTrue($resolver->hasVersioning());
        $this->assertTrue($resolver->hasVersioning());
    }

    public function testApplyToSelectAddsBothPredicatesWithGivenPrefixWhenVersioningIsPresent(): void
    {
        $resolver = $this->resolver(hasVersioning: true);
        $select = new RecordingSelect();

        $resolver->applyToSelect($select, 'e.');

        $this->assertSame(
            ['e.created_in <= UNIX_TIMESTAMP()', 'e.updated_in > UNIX_TIMESTAMP()'],
            $select->wheres
        );
    }

    /**
     * Sin prefijo (el caso de DeltaReader, que selecciona directamente de
     * catalog_product_entity sin alias): el prefijo por defecto es cadena
     * vacía, no 'e.'.
     */
    public function testApplyToSelectAddsBothPredicatesWithoutPrefixByDefault(): void
    {
        $resolver = $this->resolver(hasVersioning: true);
        $select = new RecordingSelect();

        $resolver->applyToSelect($select);

        $this->assertSame(
            ['created_in <= UNIX_TIMESTAMP()', 'updated_in > UNIX_TIMESTAMP()'],
            $select->wheres
        );
    }

    public function testApplyToSelectAddsNothingWhenVersioningIsAbsent(): void
    {
        $resolver = $this->resolver(hasVersioning: false);
        $select = new RecordingSelect();

        $resolver->applyToSelect($select, 'e.');

        $this->assertSame([], $select->wheres);
    }

    /**
     * S0 Task A3: `catalog_category_entity` también es staged (row_id,
     * created_in/updated_in) — hallazgo verificado contra la instancia de
     * referencia y no mencionado por el brief original de la tarea.
     * CategoryReader debe poder pedirle a esta MISMA clase que pruebe el
     * esquema de `catalog_category_entity`, no el de
     * `catalog_product_entity`.
     */
    public function testHasVersioningChecksTheGivenTableNotJustCatalogProductEntity(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')
            ->with('catalog_category_entity', $this->logicalOr('created_in', 'updated_in'))
            ->willReturn(true);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new ActiveVersionResolver($resource);

        $this->assertTrue($resolver->hasVersioning('catalog_category_entity'));
    }

    /**
     * Memoización por tabla, no global: si `catalog_product_entity` ya se
     * probó como versionada, una tabla distinta sin esas columnas no debe
     * heredar ese resultado cacheado.
     */
    public function testHasVersioningMemoizationIsPerTableNotGlobal(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => $table === 'catalog_product_entity'
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new ActiveVersionResolver($resource);

        $this->assertTrue($resolver->hasVersioning('catalog_product_entity'));
        $this->assertFalse($resolver->hasVersioning('catalog_category_entity'));
        $this->assertTrue($resolver->hasVersioning('catalog_product_entity'));
    }

    /**
     * applyToSelect() para una tabla que no es catalog_product_entity debe
     * probar el esquema de ESA tabla, no la por-defecto.
     */
    public function testApplyToSelectUsesTheGivenTableToDecideWhetherToFilter(): void
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => $table === 'catalog_category_entity'
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $resolver = new ActiveVersionResolver($resource);
        $select = new RecordingSelect();

        $resolver->applyToSelect($select, 'e.', 'catalog_category_entity');

        $this->assertSame(
            ['e.created_in <= UNIX_TIMESTAMP()', 'e.updated_in > UNIX_TIMESTAMP()'],
            $select->wheres
        );
    }

    private function resolver(bool $hasVersioning): ActiveVersionResolver
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')
            ->with('catalog_product_entity', $this->logicalOr('created_in', 'updated_in'))
            ->willReturn($hasVersioning);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new ActiveVersionResolver($resource);
    }
}

/**
 * Doble mínimo de `Magento\Framework\DB\Select`: solo registra los
 * where() reales que arma ActiveVersionResolver::applyToSelect(), sin tocar
 * ninguna base de datos.
 */
final class RecordingSelect extends \Magento\Framework\DB\Select
{
    /** @var list<string> */
    public array $wheres = [];

    public function __construct()
    {
    }

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[] = (string) $cond;
        return $this;
    }
}
