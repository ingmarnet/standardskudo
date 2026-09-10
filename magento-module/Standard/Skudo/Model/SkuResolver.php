<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Traduce los IDS que llevan los eventos de escritura masiva de Magento a los
 * SKUS que guarda la cola de cambios.
 *
 * Existe por H1. `Product\Action::updateAttributes()` —la acción masiva
 * "Actualizar atributos" del grid de admin y la API que usan las
 * herramientas de carga por lotes— y `Product\Action::updateWebsites()`
 * despachan sus eventos con `product_ids` / `products`, nunca con SKUs. La
 * cola (`standard_skudo_change_log`) es por SKU porque el SKU es la
 * identidad del producto para el resto del sistema (el espejo, `/deltas`,
 * `/products-by-sku`), así que alguien tiene que traducir, y esa traducción
 * tiene un modo de fallo silencioso que hay que cerrar con cuidado.
 *
 * LA DECISIÓN QUE IMPORTA: los ids de esos eventos son `entity_id`, NO la
 * clave de la tabla. Bajo Magento_Staging `catalog_product_entity` está
 * clavada por `row_id` y `entity_id` es una columna más; el propio Magento
 * lo trata así —`ResourceModel\Product\Action::resolveEntityId()` traduce el
 * id recibido a la clave con `WHERE entity_id = ?`— y por lo tanto el
 * contrato del evento es inequívoco. Emparejar por la clave (`row_id`) en
 * una instancia versionada no da error: da el SKU de OTRO producto, porque
 * el espacio de `row_id` y el de `entity_id` se solapan. La cola quedaría
 * con un cambio anotado sobre un producto que nadie tocó, y el que cambió de
 * verdad no se refrescaría nunca. Un fallo silencioso y permanente, que es
 * exactamente lo que H1 existe para cerrar.
 *
 * Se consulta `EntityKeyResolver` y no se ignora el asunto porque su
 * respuesta explica el otro caso: cuando la clave es `row_id`, un mismo
 * `entity_id` puede tener MÁS de una fila (una por versión), y la consulta
 * puede devolver más filas que ids pedidos. Magento acota todo `Select` del
 * framework a la versión aplicada, así que en la práctica vuelve una; cuando
 * vuelven varias con SKUs distintos (un cambio de SKU programado) se
 * devuelven TODOS los SKUs distintos. La asimetría del coste decide:
 * registrar un SKU de sobra cuesta un refresco redundante —todo el camino de
 * refresco del ingestor es upsert por (tenant, sku, store view)—, perder el
 * que cambió cuesta un valor rancio indefinido.
 *
 * Sin filtro de versión propio (ver `Model\VersioningSchema`): el `FROM` de
 * una tabla staged ya lo recibe del framework, y un segundo filtro anclado
 * en otra cosa es el defecto C2.
 */
class SkuResolver
{
    /**
     * Ids por consulta. Una acción masiva del grid puede llevar decenas de
     * miles de ids y un `IN (...)` de ese tamaño es una consulta que MySQL
     * puede rechazar por `max_allowed_packet`. El mismo criterio que
     * `PRODUCTS_BY_SKU_CHUNK` del lado Python, con más holgura porque acá son
     * enteros.
     */
    public const CHUNK = 1000;

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly EntityKeyResolver $entityKeyResolver,
    ) {
    }

    /**
     * @param int[] $entityIds
     */
    public function resolve(array $entityIds): SkuResolution
    {
        $wanted = [];
        foreach ($entityIds as $id) {
            $id = (int) $id;
            if ($id > 0) {
                $wanted[$id] = true;
            }
        }
        if ($wanted === []) {
            return new SkuResolution([], []);
        }

        $connection = $this->resource->getConnection();
        $entity = $this->resource->getTableName('catalog_product_entity');
        // Se pide la clave además del par (entity_id, sku) para que quede a la
        // vista, en la consulta misma, que la clave NO es lo que se empareja.
        $keyColumn = $this->entityKeyResolver->resolve();

        $skus = [];
        $found = [];
        foreach (array_chunk(array_keys($wanted), self::CHUNK) as $chunk) {
            $select = $connection->select()
                ->from(['e' => $entity], [
                    'key' => 'e.' . $keyColumn,
                    'entity_id' => 'e.entity_id',
                    'sku' => 'e.sku',
                ])
                ->where('e.entity_id IN (?)', $chunk);

            foreach ($connection->fetchAll($select) as $row) {
                $found[(int) $row['entity_id']] = true;
                $skus[(string) $row['sku']] = true;
            }
        }

        $unresolved = array_values(array_diff(array_keys($wanted), array_keys($found)));
        $skus = array_keys($skus);
        sort($skus, SORT_STRING);

        return new SkuResolution($skus, $unresolved);
    }
}
