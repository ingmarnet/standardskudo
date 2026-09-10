<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Select;
use Magento\Framework\Exception\InputException;
use Standard\Skudo\Api\ProductReaderInterface;

class ProductReader implements ProductReaderInterface
{
    private const MAX_LIMIT = 1000;

    /** Tope de SKUs por llamada a getBySku(). Ver ProductReaderInterface. */
    private const MAX_SKUS = 100;

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly Cursor $cursor,
        private readonly EntityKeyResolver $entityKeyResolver,
        // Inyectado, no instanciado con `new`, por la misma razón que
        // EntityKeyResolver: DeltaReader (Task 10) inyecta la MISMA clase,
        // así que ambos comparten una única instancia memoizada del
        // esquema y una única definición de la regla de versión activa.
        // Ver ActiveVersionResolver.
        private readonly ActiveVersionResolver $activeVersionResolver,
    ) {
    }

    public function getPage(int $storeId, int $limit = 500, ?string $cursor = null): array
    {
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $after = $this->cursor->decode($cursor);
        $keyColumn = $this->entityKeyResolver->resolve();

        $select = $this->baseEntitySelect($keyColumn)
            ->where('e.' . $keyColumn . ' > ?', $after)
            ->order('e.' . $keyColumn . ' ASC')
            ->limit($limit);
        $this->applyActiveVersionFilter($select);

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
        $this->applyActiveVersionFilter($select);

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
     * Restringe la consulta de entidad a la versión activa cuando el esquema
     * la soporta (Magento_Staging: created_in/updated_in acotan la ventana de
     * validez de cada versión de un mismo producto).
     *
     * Sin este filtro, `catalog_product_entity` no tiene una fila por
     * producto sino una por versión, y como la paginación recorre la clave
     * de forma ascendente, una versión programada a futuro siempre tiene la
     * clave más alta: ganaría el upsert sobre la versión vigente.
     *
     * La detección del esquema y la definición de la ventana viven en
     * ActiveVersionResolver, no acá: ver esa clase para el porqué.
     *
     * Como las tablas EAV se indexan por la misma clave que la fila de
     * entidad, acotar la entidad a la versión activa ya deja solo esos
     * valores: las tablas EAV no se filtran aparte.
     */
    private function applyActiveVersionFilter(Select $select): void
    {
        $this->activeVersionResolver->applyToSelect($select, 'e.');
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
                'global_values' => $globalValues,
                'store_values' => $stores[$key] ?? [],
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
        // Igual que en la consulta de entidad: bajo Magento_Staging
        // `catalog_product_entity` tiene una fila por VERSIÓN, así que unir por
        // sku multiplica cada enlace de categoría por el número de versiones
        // (caso real verificado: `NGO-T2092` tiene 3 filas de versión y esta
        // consulta devolvía la categoría 603 tres veces). El espejo quedaba
        // correcto solo porque `set_product_categories` deduplica del otro lado
        // del cable — apoyarse en eso es apoyarse en un detalle del consumidor,
        // y el inflado es proporcional a las versiones sobre cientos de miles
        // de upserts.
        $this->applyActiveVersionFilter($select);

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(string) $row['sku']][] = (int) $row['category_id'];
        }
        return $out;
    }
}
