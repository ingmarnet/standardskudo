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

    private readonly EntityKeyResolver $keyResolver;

    public function __construct(
        private readonly ProductMetadataInterface $metadata,
        private readonly ModuleListInterface $modules,
        private readonly ResourceConnection $resource,
        private readonly StoreManagerInterface $storeManager,
    ) {
        // Un único lugar decide row_id vs. entity_id; ver EntityKeyResolver.
        $this->keyResolver = new EntityKeyResolver($resource);
    }

    public function getProfile(): array
    {
        $hasStaging = $this->modules->has('Magento_Staging');

        return [
            'edition' => $this->metadata->getEdition(),
            'version' => $this->metadata->getVersion(),
            // La clave se detecta del esquema, no de la edición: Commerce sin
            // Staging instalado sigue usando entity_id.
            'product_entity_key' => $this->keyResolver->resolve(),
            'staging_enabled' => $hasStaging,
            'msi_enabled' => $this->modules->has('Magento_InventoryApi'),
            'default_stock_id' => $this->modules->has('Magento_InventoryApi') ? 1 : null,
            'websites' => $this->websites(),
            'store_groups' => $this->storeGroups(),
            'store_views' => $this->storeViews(),
            'counts' => $this->counts(),
            'module_version' => self::MODULE_VERSION,
        ];
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
