<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Resuelve la clave de entidad de `catalog_product_entity` (row_id con
 * Magento_Staging activo, entity_id en su ausencia) probando el esquema una
 * sola vez por instancia.
 *
 * La detección se hace SIEMPRE con tableColumnExists(), nunca a partir de la
 * edición ni de la lista de módulos: un tenant Commerce sin Staging sigue
 * usando entity_id, y este módulo debe funcionar igual en Community.
 *
 * El resultado se memoiza porque un lector que pagina cientos de miles de
 * productos llamaría esta detección en cada página; probar el esquema una
 * vez por request en lugar de una vez por página es la diferencia entre una
 * consulta y cientos.
 */
class EntityKeyResolver
{
    private ?string $key = null;

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function resolve(): string
    {
        if ($this->key === null) {
            $connection = $this->resource->getConnection();
            $table = $this->resource->getTableName('catalog_product_entity');
            $this->key = $connection->tableColumnExists($table, 'row_id') ? 'row_id' : 'entity_id';
        }

        return $this->key;
    }
}
