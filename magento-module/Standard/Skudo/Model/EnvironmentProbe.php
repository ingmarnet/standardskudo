<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\StoreManagerInterface;
use Standard\Skudo\Api\EnvironmentProbeInterface;

class EnvironmentProbe implements EnvironmentProbeInterface
{
    private const MODULE_VERSION = '1.0.0';

    public function __construct(
        private readonly ProductMetadataInterface $metadata,
        private readonly ModuleListInterface $modules,
        private readonly ResourceConnection $resource,
        private readonly StoreManagerInterface $storeManager,
        // Inyectado, no instanciado con `new`: EntityKeyResolver es
        // "shared" para el ObjectManager, así que ProductReader y esta
        // clase reciben la MISMA instancia memoizada y el esquema se
        // prueba una sola vez por request, no una vez por cada una.
        private readonly EntityKeyResolver $keyResolver,
    ) {
    }

    public function getProfile(): array
    {
        $hasStaging = $this->modules->has('Magento_Staging');
        $hasMsi = $this->modules->has('Magento_InventoryApi');

        return WebApiEnvelope::wrap([
            'edition' => $this->metadata->getEdition(),
            'version' => $this->metadata->getVersion(),
            // La clave se detecta del esquema, no de la edición: Commerce sin
            // Staging instalado sigue usando entity_id.
            'product_entity_key' => $this->keyResolver->resolve(),
            'staging_enabled' => $hasStaging,
            'msi_enabled' => $hasMsi,
            'default_stock_id' => $this->defaultStockId($hasMsi),
            'websites' => $this->websites(),
            'store_groups' => $this->storeGroups(),
            'store_views' => $this->storeViews(),
            'counts' => $this->counts(),
            'module_version' => self::MODULE_VERSION,
        ]);
    }

    /**
     * El stock de MSI que sirve a esta instancia, o null si no hay UNO solo.
     *
     * Antes devolvía 1 en cuanto MSI estuviera presente. Verificado contra la
     * instancia de referencia (solo lectura): los canales de venta mapean
     * `base -> 2` y `website_br -> 3`, y el Stock 1 ("Default Stock") no
     * está asignado a ningún sitio — el 1 no era un valor genérico, era el
     * valor equivocado. `SignalReader::resolveStockId()` ya resolvía esto bien
     * para su caso (store -> website -> canal de venta -> stock_id) y devuelve
     * null en vez de adivinar; esta sonda, cuyo propósito declarado es que
     * "nada aquí se asume", codificaba la respuesta contraria.
     *
     * La sonda no tiene store view: informa una propiedad de la instancia. Por
     * eso solo puede responder cuando la respuesta es INEQUÍVOCA — exactamente
     * un stock mapeado por los canales de venta—, y dice null cuando hay
     * varios (no existe "el" stock por defecto: depende del sitio, y quien
     * necesite esa precisión la obtiene por store view de `/signals`), cuando
     * no hay ninguno, cuando MSI está ausente, o cuando el módulo figura
     * instalado pero su tabla no existe (Ruling 1 de Task 12: las dos
     * direcciones de esa discrepancia se dan en la práctica).
     *
     * No referencia ninguna clase de `Magento\\InventorySalesApi\\*`: se lee la
     * tabla, para que este módulo cargue igual en una instancia sin MSI.
     */
    private function defaultStockId(bool $hasMsi): ?int
    {
        if (!$hasMsi) {
            return null;
        }

        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName('inventory_stock_sales_channel');
        if (!$connection->isTableExists($table)) {
            return null;
        }

        $stockIds = array_values(array_unique(array_map(
            'intval',
            $connection->fetchCol($connection->select()->from($table, ['stock_id']))
        )));

        return count($stockIds) === 1 ? $stockIds[0] : null;
    }

    private function websites(): array
    {
        $out = [];
        foreach ($this->storeManager->getWebsites() as $website) {
            $out[] = [
                'id' => (int) $website->getId(),
                'code' => (string) $website->getCode(),
                'name' => (string) $website->getName(),
            ];
        }
        return $out;
    }

    private function storeGroups(): array
    {
        $out = [];
        foreach ($this->storeManager->getGroups() as $group) {
            $out[] = [
                'id' => (int) $group->getId(),
                'website_id' => (int) $group->getWebsiteId(),
                'code' => (string) $group->getCode(),
                'name' => (string) $group->getName(),
                'root_category_id' => (int) $group->getRootCategoryId(),
            ];
        }
        return $out;
    }

    private function storeViews(): array
    {
        $out = [];
        foreach ($this->storeManager->getStores() as $store) {
            $out[] = [
                'id' => (int) $store->getId(),
                'group_id' => (int) $store->getStoreGroupId(),
                'code' => (string) $store->getCode(),
                'name' => (string) $store->getName(),
                'is_active' => (bool) $store->isActive(),
                'locale' => (string) $store->getConfig('general/locale/code'),
                'currency' => (string) $store->getCurrentCurrencyCode(),
            ];
        }
        return $out;
    }

    private function counts(): array
    {
        $connection = $this->resource->getConnection();
        $count = function (string $table) use ($connection): int {
            $name = $this->resource->getTableName($table);
            return (int) $connection->fetchOne(
                $connection->select()->from($name, 'COUNT(*)')
            );
        };

        return [
            'products' => $count('catalog_product_entity'),
            'attribute_sets' => $count('eav_attribute_set'),
            'attributes' => $count('eav_attribute'),
            'categories' => $count('catalog_category_entity'),
        ];
    }
}
