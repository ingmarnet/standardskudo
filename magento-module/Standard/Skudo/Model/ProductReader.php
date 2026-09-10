<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Select;
use Magento\Framework\Exception\InputException;
use Standard\Skudo\Api\ProductReaderInterface;

/**
 * Este lector NO acota la consulta a la versión activa: no agrega ni un
 * `created_in` ni un `updated_in` propios. Bajo Magento_Staging,
 * `catalog_product_entity` tiene una fila por VERSIÓN y no por producto,
 * pero Magento ya inyecta su propia ventana —anclada en el id de la versión
 * APLICADA— en todo `Select` del framework que haga FROM de una tabla
 * staged, incluidos los joins. Un segundo filtro anclado en el reloj
 * (lo que este lector hacía) es una definición DISTINTA de "activa" y su
 * conjunción con la de Magento pierde filas que la tienda sí muestra.
 * Ver `Model\VersioningSchema` para la medición y el porqué completo, y
 * `Test/Unit/Model/NoOwnVersionFilterTest` para la prueba que impide que
 * el filtro vuelva.
 */
class ProductReader implements ProductReaderInterface
{
    private const MAX_LIMIT = 1000;

    /** Tope de SKUs por llamada a getBySku(). Ver ProductReaderInterface. */
    private const MAX_SKUS = 100;

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly Cursor $cursor,
        private readonly EntityKeyResolver $entityKeyResolver,
        // M3: el storeId lo provee la red y hasta ahora nadie comprobaba que
        // existiera. Ver Model\StoreViewGuard.
        private readonly StoreViewGuard $storeViewGuard,
    ) {
    }

    public function getPage(int $storeId, int $limit = 500, ?string $cursor = null): array
    {
        $this->storeViewGuard->assertExists($storeId);
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $after = $this->cursor->decode($cursor);
        $keyColumn = $this->entityKeyResolver->resolve();

        $select = $this->baseEntitySelect($keyColumn)
            ->where('e.' . $keyColumn . ' > ?', $after)
            ->order('e.' . $keyColumn . ' ASC')
            ->limit($limit);

        $rows = $this->resource->getConnection()->fetchAll($select);
        if ($rows === []) {
            return WebApiEnvelope::wrap(['items' => [], 'next_cursor' => null]);
        }

        $items = $this->projectRows($rows, $keyColumn, $storeId);
        $keys = array_column($rows, 'key');

        return WebApiEnvelope::wrap([
            'items' => $items,
            'next_cursor' => count($rows) < $limit
                ? null
                : $this->cursor->encode((int) $keys[array_key_last($keys)]),
        ]);
    }

    public function getBySku(int $storeId, array $skus): array
    {
        $this->storeViewGuard->assertExists($storeId);

        if (count($skus) > self::MAX_SKUS) {
            throw new InputException(__(
                'no se pueden pedir más de %1 SKUs por llamada (se recibieron %2)',
                self::MAX_SKUS,
                count($skus)
            ));
        }

        if ($skus === []) {
            return WebApiEnvelope::wrap(['items' => []]);
        }

        $keyColumn = $this->entityKeyResolver->resolve();

        $select = $this->baseEntitySelect($keyColumn)
            // Identidad como texto, sin normalizar: 'sku' viaja tal cual.
            ->where('e.sku IN (?)', $skus);

        $rows = $this->resource->getConnection()->fetchAll($select);

        return WebApiEnvelope::wrap(['items' => $this->projectRows($rows, $keyColumn, $storeId)]);
    }

    /**
     * Proyección compartida por getPage() y getBySku(): las mismas cinco
     * columnas de `catalog_product_entity`, sin ningún where/order/limit
     * todavía. Cada caller agrega solo lo que lo distingue (el keyset y el
     * orden en getPage(), el `sku IN (?)` en getBySku()) para que un campo
     * agregado a un lado y no al otro no pueda pasar inadvertido.
     */
    private function baseEntitySelect(string $keyColumn): Select
    {
        $entity = $this->resource->getTableName('catalog_product_entity');

        return $this->resource->getConnection()
            ->select()
            ->from(['e' => $entity], [
                'key' => 'e.' . $keyColumn,
                'sku' => 'e.sku',
                'attribute_set_id' => 'e.attribute_set_id',
                'type_id' => 'e.type_id',
                'updated_at' => 'e.updated_at',
            ]);
    }

    /**
     * @param mixed[] $rows
     * @return mixed[]
     */
    private function projectRows(array $rows, string $keyColumn, int $storeId): array
    {
        if ($rows === []) {
            return [];
        }

        $keys = array_column($rows, 'key');
        $globals = $this->eavValues($keyColumn, $keys, 0);
        $stores = $this->eavValues($keyColumn, $keys, $storeId);
        $websites = $this->websiteIds($keys, $keyColumn);
        $categories = $this->categoryIds(array_column($rows, 'sku'));

        $items = [];
        foreach ($rows as $row) {
            $key = (int) $row['key'];
            $globalValues = $globals[$key] ?? [];
            $items[] = [
                'sku' => (string) $row['sku'],
                // Identidad desglosada; todo texto, sin normalizar.
                'mpn' => $globalValues['mpn'] ?? null,
                'model' => $globalValues['model'] ?? null,
                'gtin' => $globalValues['gtin'] ?? null,
                'variant_key' => $globalValues['variant_key'] ?? null,
                'attribute_set_id' => (int) $row['attribute_set_id'],
                'type_id' => (string) $row['type_id'],
                // (object), no el array PHP: un mapa codigo -> valor VACIO
                // (`[]`) es indistinguible de una lista para json_encode() y
                // llega al cliente como `[]` en vez de `{}`. Es el MISMO bug
                // que AttributeReader ya neutraliza en `labels` (ver el
                // Ruling 2 de esa clase), aplicado al mapa de valores EAV:
                // verificado sobre HTTP real, `store_values: []` hacia
                // estallar resolve_scope() del lado Python con
                // `AttributeError: 'list' object has no attribute 'items'`
                // en el primer producto sin override de store view.
                'global_values' => (object) $globalValues,
                'store_values' => (object) ($stores[$key] ?? []),
                'website_ids' => $websites[$key] ?? [],
                'category_ids' => $categories[(string) $row['sku']] ?? [],
                'updated_at' => (string) $row['updated_at'],
            ];
        }

        return $items;
    }

    /**
     * Valores EAV de un scope concreto, agrupados por clave de entidad.
     *
     * store_id = 0 es el valor global; store_id = N el override de esa store view.
     * La ausencia de fila en N es lo que significa "hereda"; por eso los dos
     * scopes se leen por separado y se resuelven en el ingestor, no aquí.
     *
     * @param int[] $keys
     * @return array<int, array<string, string>>
     */
    private function eavValues(string $keyColumn, array $keys, int $storeId): array
    {
        $connection = $this->resource->getConnection();
        $attribute = $this->resource->getTableName('eav_attribute');
        $out = [];

        foreach (['varchar', 'int', 'decimal', 'text', 'datetime'] as $type) {
            $table = $this->resource->getTableName('catalog_product_entity_' . $type);
            $select = $connection->select()
                ->from(['v' => $table], ['entity' => 'v.' . $keyColumn, 'value' => 'v.value'])
                ->join(['a' => $attribute], 'a.attribute_id = v.attribute_id', ['code' => 'a.attribute_code'])
                ->where('v.' . $keyColumn . ' IN (?)', $keys)
                ->where('v.store_id = ?', $storeId);

            foreach ($connection->fetchAll($select) as $row) {
                $out[(int) $row['entity']][(string) $row['code']] = $row['value'] === null
                    ? null
                    : (string) $row['value'];
            }
        }

        return $out;
    }

    /**
     * @param int[] $keys
     * @return array<int, int[]>
     */
    private function websiteIds(array $keys, string $keyColumn): array
    {
        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName('catalog_product_website');
        $entity = $this->resource->getTableName('catalog_product_entity');

        // catalog_product_website siempre referencia entity_id, incluso con Staging.
        $select = $connection->select()
            ->from(['w' => $table], ['website_id' => 'w.website_id'])
            ->join(['e' => $entity], 'e.entity_id = w.product_id', ['key' => 'e.' . $keyColumn])
            ->where('e.' . $keyColumn . ' IN (?)', $keys);

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(int) $row['key']][] = (int) $row['website_id'];
        }
        return $out;
    }

    /**
     * @param string[] $skus
     * @return array<string, int[]>
     */
    private function categoryIds(array $skus): array
    {
        $connection = $this->resource->getConnection();
        $link = $this->resource->getTableName('catalog_category_product');
        $entity = $this->resource->getTableName('catalog_product_entity');

        // Sin store_id: la asignación es global. El efecto por tienda lo deriva
        // el ingestor con derive_category_effect.
        $select = $connection->select()
            ->from(['l' => $link], ['category_id' => 'l.category_id'])
            ->join(['e' => $entity], 'e.entity_id = l.product_id', ['sku' => 'e.sku'])
            ->where('e.sku IN (?)', $skus);
        // Sin filtro de versión propio: el join a `catalog_product_entity`
        // es un FROM de tabla staged, así que Magento ya lo acota a la
        // versión aplicada y deja como máximo una fila por entidad. La
        // duplicación que este método temía (un sku con tres filas de
        // versión devolviendo la misma categoría tres veces) no es
        // reproducible en una petición real: verificado sobre la instancia
        // de desarrollo, `categoryIds()` devuelve cada categoría una vez.

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(string) $row['sku']][] = (int) $row['category_id'];
        }
        return $out;
    }
}
