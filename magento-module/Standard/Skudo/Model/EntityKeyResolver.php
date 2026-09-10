<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Resuelve la clave de entidad de una tabla staged (row_id con
 * Magento_Staging activo, entity_id en su ausencia) probando el esquema una
 * sola vez por tabla por instancia.
 *
 * La detección se hace SIEMPRE con tableColumnExists(), nunca a partir de la
 * edición ni de la lista de módulos: un tenant Commerce sin Staging sigue
 * usando entity_id, y este módulo debe funcionar igual en Community.
 *
 * El resultado se memoiza por tabla porque un lector que pagina cientos de
 * miles de productos llamaría esta detección en cada página; probar el
 * esquema una vez por request en lugar de una vez por página es la
 * diferencia entre una consulta y cientos.
 *
 * Parametrizada por tabla (S0 Task A3): `catalog_category_entity` es staged
 * igual que `catalog_product_entity` (misma pregunta de esquema, tabla
 * distinta), y CategoryReader reutiliza esta MISMA clase pasándole su
 * nombre de tabla, en vez de una tercera copia de la sonda. El default de
 * `catalog_product_entity` preserva el comportamiento de los callers
 * existentes (ProductReader, DeltaReader, SignalReader, EnvironmentProbe),
 * que no necesitan cambiar.
 */
class EntityKeyResolver
{
    private const DEFAULT_TABLE = 'catalog_product_entity';

    /** @var array<string, string> */
    private array $keysByTable = [];

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function resolve(string $table = self::DEFAULT_TABLE): string
    {
        if (!isset($this->keysByTable[$table])) {
            $connection = $this->resource->getConnection();
            $tableName = $this->resource->getTableName($table);
            $this->keysByTable[$table] = $connection->tableColumnExists($tableName, 'row_id')
                ? 'row_id'
                : 'entity_id';
        }

        return $this->keysByTable[$table];
    }
}
