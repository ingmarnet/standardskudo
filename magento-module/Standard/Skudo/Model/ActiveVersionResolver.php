<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Select;

/**
 * Único lugar que conoce la regla de "versión activa" de Magento_Staging:
 * si `catalog_product_entity` tiene `created_in`/`updated_in`, y si las
 * tiene, la ventana que hace que una fila sea la vigente
 * (`created_in <= ahora AND updated_in > ahora`).
 *
 * Antes de esta clase, ProductReader (Task 8) y DeltaReader (Task 10)
 * reimplementaban cada uno su propia detección memoizada y su propia copia
 * de los dos `where()`. Dos lugares respondiendo la misma pregunta de
 * esquema y codificando la misma regla de ventana es exactamente el
 * problema que este proyecto existe para evitar: el día que la regla
 * cambie (otro reloj, un límite `>=` en vez de `>`, una instalación con las
 * columnas presentes pero sin usar) y se cambie en uno solo de los dos
 * lugares, el lector masivo y el de deltas quedarían en desacuerdo sobre
 * qué versión de un producto está viva. Esta clase es el precedente de
 * `EntityKeyResolver` aplicado a esa segunda pregunta de esquema.
 *
 * La detección se hace SIEMPRE con tableColumnExists(), nunca a partir de
 * la edición ni de la lista de módulos, y se memoiza por tabla por la
 * misma razón que EntityKeyResolver: un lector que pagina cientos de miles
 * de productos, o un endpoint de deltas que se llama en cada ciclo de
 * polling, no debe repetir la prueba de esquema en cada llamada.
 *
 * Parametrizada por tabla (S0 Task A3): `catalog_category_entity` también
 * es staged — hallazgo verificado contra la instancia de referencia y no
 * mencionado por el brief original de esa tarea — así que CategoryReader
 * reutiliza esta MISMA clase pidiéndole la ventana de versión activa para
 * su propia tabla, en vez de una tercera copia de la regla. El default de
 * `catalog_product_entity` preserva el comportamiento de ProductReader y
 * DeltaReader, que no necesitan cambiar.
 */
class ActiveVersionResolver
{
    private const DEFAULT_TABLE = 'catalog_product_entity';

    /** @var array<string, bool> */
    private array $hasVersioningByTable = [];

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function hasVersioning(string $table = self::DEFAULT_TABLE): bool
    {
        if (!isset($this->hasVersioningByTable[$table])) {
            $connection = $this->resource->getConnection();
            $entity = $this->resource->getTableName($table);
            $this->hasVersioningByTable[$table] = $connection->tableColumnExists($entity, 'created_in')
                && $connection->tableColumnExists($entity, 'updated_in');
        }

        return $this->hasVersioningByTable[$table];
    }

    /**
     * Restringe $select a la versión activa, cuando el esquema la soporta;
     * no hace nada cuando no la soporta (Community, o Commerce sin Staging),
     * porque ahí referenciar created_in/updated_in sería un error de SQL.
     *
     * $columnPrefix es el prefijo con el que esa consulta nombra las
     * columnas de la tabla ($table): el alias de tabla seguido de un punto
     * (p.ej. 'e.') si la consulta usa alias, o cadena vacía si selecciona
     * directamente de la tabla sin alias. $table es la tabla cuyo esquema
     * se prueba (por defecto catalog_product_entity, para no romper a los
     * callers existentes).
     */
    public function applyToSelect(Select $select, string $columnPrefix = '', string $table = self::DEFAULT_TABLE): void
    {
        if ($this->hasVersioning($table)) {
            $select->where($columnPrefix . 'created_in <= UNIX_TIMESTAMP()')
                ->where($columnPrefix . 'updated_in > UNIX_TIMESTAMP()');
        }
    }
}
