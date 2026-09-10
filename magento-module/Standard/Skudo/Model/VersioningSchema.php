<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Responde UNA sola pregunta de esquema: ¿esta tabla tiene las columnas de
 * versionado (`created_in`/`updated_in`) que instala Magento_Staging?
 *
 * Lo que esta clase YA NO hace, y el porqué (S0, ronda de verificación
 * sobre HTTP real): antes se llamaba `ActiveVersionResolver` y además de
 * detectar las columnas agregaba a cada `Select` del módulo su propia
 * ventana de versión activa —`created_in <= UNIX_TIMESTAMP() AND updated_in
 * > UNIX_TIMESTAMP()`—. Ese filtro estaba de más y hacía daño.
 *
 * De más: Magento ya acota TODO `Select` construido por el framework sobre
 * una tabla staged. El renderer de la parte FROM que Adobe Commerce
 * registra inyecta, al ensamblarse el SQL,
 *
 *     AND <alias>.created_in <= :v AND <alias>.updated_in > :v
 *
 * donde `:v` es el id de la versión APLICADA (el flag `staging` de la
 * tabla `flag`, que el cron de aplicación de versiones adelanta). Ese
 * filtro llega a cada consulta de este módulo sin que el módulo haga nada,
 * porque todas se construyen con `$connection->select()`.
 *
 * Daño: los dos filtros no son el mismo criterio. Magento compara contra el
 * ID DE LA VERSIÓN APLICADA; el filtro del módulo comparaba contra el
 * RELOJ. Su conjunción es estrictamente más estrecha que cualquiera de los
 * dos, y la diferencia no es teórica: verificado sobre HTTP real contra la
 * instancia de desarrollo con `current_version = 1789020000`, Magento
 * considera activas 8 filas de `catalog_product_entity` y el módulo
 * devolvía 7 — descartaba la versión de `SKU-EXPIRED`, cuyo `updated_in` ya
 * pasó por reloj pero que Magento sigue sirviendo porque su expiración
 * todavía no fue aplicada. Un producto que la tienda muestra y el módulo no
 * reporta.
 *
 * Y lo peor: `/checksums` sufría exactamente el mismo doble filtro que
 * `/products`, así que los dos lados del espejo coincidían sobre el
 * conjunto estrechado y `reconcile()` reportaba "sin deriva" para siempre.
 * La red de seguridad quedaba estructuralmente ciega al hueco.
 *
 * En Community las columnas no existen, así que el filtro del módulo nunca
 * aplicó ahí tampoco. En ninguna edición aportaba nada.
 *
 * Queda la detección, y sólo la detección, porque `DeltaReader` la
 * necesita de verdad: su consulta de activaciones nombra `created_in`
 * explícitamente (`created_in > $sinceTimestamp`), y en una instalación sin
 * versionado esa columna no existe y la consulta sería un error de SQL. La
 * pregunta se responde SIEMPRE con `tableColumnExists()`, nunca a partir de
 * la edición ni de la lista de módulos —una instancia puede tener las
 * columnas sin el módulo declarado, y al revés— y se memoiza por tabla por
 * la misma razón que `EntityKeyResolver`: un endpoint de deltas que se
 * llama en cada ciclo de polling no debe repetir la prueba de esquema en
 * cada llamada.
 *
 * Parametrizada por tabla porque `catalog_category_entity` también es
 * staged; el default de `catalog_product_entity` es el caso de DeltaReader.
 */
class VersioningSchema
{
    public const DEFAULT_TABLE = 'catalog_product_entity';

    /** @var array<string, bool> */
    private array $isVersionedByTable = [];

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function isVersioned(string $table = self::DEFAULT_TABLE): bool
    {
        if (!isset($this->isVersionedByTable[$table])) {
            $connection = $this->resource->getConnection();
            $entity = $this->resource->getTableName($table);
            $this->isVersionedByTable[$table] = $connection->tableColumnExists($entity, 'created_in')
                && $connection->tableColumnExists($entity, 'updated_in');
        }

        return $this->isVersionedByTable[$table];
    }
}
