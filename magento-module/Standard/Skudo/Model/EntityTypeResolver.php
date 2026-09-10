<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\Exception\LocalizedException;

/**
 * Único lugar que traduce un `entity_type_code` a su `entity_type_id`.
 *
 * El id NUNCA se hardcodea (típicamente 4 para `catalog_product`): asumirlo
 * sería frágil ante cualquier instancia donde el EAV se haya sembrado en
 * otro orden, y esa fragilidad no se nota hasta que los conteos y las
 * páginas de atributos hablan de otro tipo de entidad.
 *
 * Existe porque la misma resolución estaba escrita TRES veces: en
 * `AttributeReader` (para `catalog_product`), en `CategoryReader` (para
 * `catalog_category`) y —al arreglar M1— hacía falta una cuarta en
 * `EnvironmentProbe`. Es el precedente de `EntityKeyResolver` aplicado a la
 * tercera pregunta de esquema: un solo lugar que la responda, memoizado por
 * código, para que un endpoint que se llama en cada ciclo no repita la
 * consulta.
 */
class EntityTypeResolver
{
    public const PRODUCT = 'catalog_product';

    public const CATEGORY = 'catalog_category';

    /** @var array<string, int> */
    private array $idsByCode = [];

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    /**
     * @throws LocalizedException cuando el código no existe. Lanzar y no
     *     devolver null es deliberado: un `entity_type_id` que no se pudo
     *     resolver haría que toda consulta que lo use devuelva las filas de
     *     OTRO tipo de entidad (o ninguna), y las dos son peores que un
     *     fallo ruidoso.
     */
    public function resolve(string $entityTypeCode = self::PRODUCT): int
    {
        if (!isset($this->idsByCode[$entityTypeCode])) {
            $connection = $this->resource->getConnection();
            $select = $connection->select()
                ->from($this->resource->getTableName('eav_entity_type'), ['entity_type_id'])
                ->where('entity_type_code = ?', $entityTypeCode);

            $entityTypeId = $connection->fetchOne($select);
            if ($entityTypeId === false) {
                throw new LocalizedException(__(
                    'no se encontró entity_type_id para "%1" en eav_entity_type',
                    $entityTypeCode
                ));
            }

            $this->idsByCode[$entityTypeCode] = (int) $entityTypeId;
        }

        return $this->idsByCode[$entityTypeCode];
    }
}
