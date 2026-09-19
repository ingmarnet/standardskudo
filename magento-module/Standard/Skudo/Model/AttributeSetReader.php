<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Standard\Skudo\Api\AttributeSetReaderInterface;

/**
 * Lee `eav_attribute_set` para dar el NOMBRE de cada attribute set —lo único
 * que el espejo no tenía para identificar un scope `attribute_set:N`—.
 *
 * Sólo los sets de `catalog_product`: los attribute sets existen por entity
 * type (customer, category, etc. tienen los suyos), y el spec sólo habla de
 * calidad de PRODUCTOS. El `entity_type_id` se resuelve con la MISMA clase que
 * usa `AttributeReader`/`CategoryReader` (ver `Model\EntityTypeResolver`), no
 * con un valor por defecto ni la edición: un id mal resuelto devolvería los
 * sets de otro entity type.
 *
 * No usa `EntityKeyResolver` ni `VersioningSchema`: `eav_attribute_set` es una
 * tabla de DEFINICIÓN, no de datos de producto, así que no está versionada por
 * Magento_Staging (misma razón que `AttributeReader`).
 */
class AttributeSetReader implements AttributeSetReaderInterface
{
    private const ENTITY_TYPE_CODE = 'catalog_product';

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly EntityTypeResolver $entityTypeResolver,
    ) {
    }

    public function getSets(): array
    {
        $connection = $this->resource->getConnection();
        $entityTypeId = $this->entityTypeResolver->resolve(self::ENTITY_TYPE_CODE);

        $select = $connection->select()
            ->from(
                ['s' => $this->resource->getTableName('eav_attribute_set')],
                ['magento_id' => 's.attribute_set_id', 'name' => 's.attribute_set_name']
            )
            ->where('s.entity_type_id = ?', $entityTypeId)
            ->order('s.attribute_set_id ASC');

        $items = [];
        foreach ($connection->fetchAll($select) as $row) {
            $items[] = [
                'magento_id' => (int) $row['magento_id'],
                'name' => (string) $row['name'],
            ];
        }

        return WebApiEnvelope::wrap(['items' => $items]);
    }
}
