<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\EnvironmentProbe;

class EnvironmentProbeTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    /**
     * @param int[]|null $salesChannelStockIds Los `stock_id` que
     *     `inventory_stock_sales_channel` mapea, o null para que la tabla no
     *     exista (MSI ausente o instalación sin su esquema).
     */
    private function probe(
        bool $hasStaging,
        bool $hasRowId,
        bool $hasMsi,
        string $edition = 'Community',
        ?array $salesChannelStockIds = null,
    ): EnvironmentProbe {
        $metadata = $this->createMock(ProductMetadataInterface::class);
        $metadata->method('getEdition')->willReturn($edition);
        $metadata->method('getVersion')->willReturn('2.4.7-p3');

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturnCallback(
            static fn (string $name): bool => match ($name) {
                'Magento_Staging' => $hasStaging,
                'Magento_InventoryApi' => $hasMsi,
                default => false,
            }
        );

        $select = $this->createMock(\Magento\Framework\DB\Select::class);
        $select->method('from')->willReturnSelf();

        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('getTableName')->willReturnArgument(0);
        $connection->method('tableColumnExists')
            ->with('catalog_product_entity', 'row_id')
            ->willReturn($hasRowId);
        // getProfile() también arma los conteos (Step 5); el mock del
        // adaptador necesita responder a select()/fetchOne() para no
        // reventar antes de llegar a las aserciones sobre product_entity_key.
        $connection->method('select')->willReturn($select);
        $connection->method('fetchOne')->willReturn('0');
        $connection->method('isTableExists')->willReturnCallback(
            static fn (string $table): bool => $table === 'inventory_stock_sales_channel'
                && $salesChannelStockIds !== null
        );
        $connection->method('fetchCol')->willReturn(
            array_map('strval', $salesChannelStockIds ?? [])
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getWebsites')->willReturn([]);
        $storeManager->method('getGroups')->willReturn([]);
        $storeManager->method('getStores')->willReturn([]);

        // Resolver real (no un mock): EnvironmentProbe ahora lo recibe
        // inyectado en vez de construirlo con `new`, y un EntityKeyResolver
        // real sobre el mismo $resource mockeado se comporta exactamente
        // como lo haría el de producción.
        $keyResolver = new EntityKeyResolver($resource);

        return new EnvironmentProbe($metadata, $modules, $resource, $storeManager, $keyResolver);
    }

    // --- M6: `default_stock_id` no se inventa ------------------------------
    //
    // Devolvía 1 siempre que MSI estuviera presente. Verificado contra la
    // instancia de referencia: los canales de venta mapean `base -> 2` y
    // `website_br -> 3`, y el Stock 1 ("Default Stock") no sirve a NINGÚN
    // sitio. `SignalReader::resolveStockId()` ya lo hacía bien —devuelve null
    // en vez de adivinar—; la sonda, cuyo propósito declarado es que "nada
    // aquí se asume", codificaba la respuesta equivocada.

    public function testDefaultStockIdIsNullWhenMsiIsAbsent(): void
    {
        $profile = $this->payloadOf($this->probe(
            hasStaging: false,
            hasRowId: false,
            hasMsi: false,
        )->getProfile());

        $this->assertNull($profile['default_stock_id']);
    }

    public function testDefaultStockIdIsNullWhenSalesChannelsPointAtSeveralStocks(): void
    {
        // El caso REAL de esta instancia: dos sitios, dos stocks distintos.
        // No existe un "stock por defecto" que responda por los dos, así que
        // la respuesta honesta es DESCONOCIDO — y el 1 que se devolvía no era
        // solo genérico: no lo usa nadie.
        $profile = $this->payloadOf($this->probe(
            hasStaging: false,
            hasRowId: false,
            hasMsi: true,
            salesChannelStockIds: [2, 3],
        )->getProfile());

        $this->assertNull(
            $profile['default_stock_id'],
            'con varios stocks mapeados no hay uno por defecto: 1 sería una respuesta inventada'
        );
    }

    public function testDefaultStockIdIsTheOnlyMappedStockWhenThereIsExactlyOne(): void
    {
        // Cuando la instancia SÍ tiene una respuesta inequívoca se reporta,
        // porque decir "desconocido" ahí también sería falso. El 7 discrimina:
        // un `return 1` seguiría pasando las dos pruebas de arriba.
        $profile = $this->payloadOf($this->probe(
            hasStaging: false,
            hasRowId: false,
            hasMsi: true,
            salesChannelStockIds: [7],
        )->getProfile());

        $this->assertSame(7, $profile['default_stock_id']);
    }

    public function testDefaultStockIdIsNullWhenMsiIsInstalledButHasNoSalesChannelTable(): void
    {
        // Ruling 1 de Task 12: el módulo puede figurar instalado sin su
        // esquema (y al revés). Sin la tabla no hay de dónde saberlo.
        $profile = $this->payloadOf($this->probe(
            hasStaging: false,
            hasRowId: false,
            hasMsi: true,
            salesChannelStockIds: null,
        )->getProfile());

        $this->assertTrue($profile['msi_enabled']);
        $this->assertNull($profile['default_stock_id']);
    }

    public function testOpenSourceReportsEntityId(): void
    {
        $profile = $this->payloadOf($this->probe(
            hasStaging: false,
            hasRowId: false,
            hasMsi: true,
            edition: 'Community',
        )->getProfile());

        $this->assertSame('Community', $profile['edition']);
        $this->assertSame('entity_id', $profile['product_entity_key']);
        $this->assertFalse($profile['staging_enabled']);
        $this->assertTrue($profile['msi_enabled']);
    }

    public function testCommerceWithStagingReportsRowId(): void
    {
        $profile = $this->payloadOf($this->probe(
            hasStaging: true,
            hasRowId: true,
            hasMsi: true,
            edition: 'Enterprise',
        )->getProfile());

        $this->assertSame('row_id', $profile['product_entity_key']);
        $this->assertTrue($profile['staging_enabled']);
    }

    public function testKeyIsDetectedFromTheSchemaNotFromTheEdition(): void
    {
        // Enterprise SIN el módulo Staging instalado (edición y bandera de
        // staging desacopladas de la clave): sigue usando entity_id porque
        // la columna row_id no existe en el esquema. Si la detección
        // mirara getEdition() en vez de tableColumnExists(), este caso
        // reportaría row_id incorrectamente y el test fallaría.
        $profile = $this->payloadOf($this->probe(
            hasStaging: false,
            hasRowId: false,
            hasMsi: false,
            edition: 'Enterprise',
        )->getProfile());

        $this->assertSame('Enterprise', $profile['edition']);
        $this->assertSame('entity_id', $profile['product_entity_key']);
        $this->assertFalse($profile['staging_enabled']);
        $this->assertFalse($profile['msi_enabled']);
    }
}
