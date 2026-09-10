<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\DB\Select;
use Magento\Framework\Exception\LocalizedException;
use Magento\Store\Model\StoreManagerInterface;
use Standard\Skudo\Api\CategoryReaderInterface;

/**
 * S0 Task A3: segundo (y último) de los dos endpoints que le dan a las
 * tablas de categoría del espejo un camino de ingesta real. Antes de esta
 * clase, `upsert_category` y `set_category_store_state` solo se llamaban
 * desde tests: `derive_category_effect` (pura, correcta, y ya probada en
 * Python) era inalcanzable con datos de espejo real porque nada escribía
 * nunca `Category.path` ni `CategoryStoreState.is_active`.
 *
 * Corrección de esquema (verificada contra la instancia de referencia,
 * solo lectura): `catalog_category_entity` es una entidad STAGED, igual
 * que `catalog_product_entity` — tiene su propio `row_id` y
 * `created_in`/`updated_in`. El brief original de esta tarea no lo
 * menciona. Por eso este lector inyecta la MISMA `EntityKeyResolver` que
 * usa `ProductReader`, pidiéndole la respuesta para
 * `catalog_category_entity` en vez de escribir una segunda copia de esa
 * detección (la clase se generalizó para aceptar el nombre de tabla; ver
 * su docblock).
 *
 * Lo que este lector NO hace, por la misma razón que `ProductReader`
 * (verificado sobre HTTP real): no agrega una ventana de versión activa
 * propia. Magento ya acota este FROM a la versión APLICADA, y de hecho la
 * versión programada a futuro de la categoría 10 de la instancia de
 * desarrollo (`row_id` 598, la clave más alta) queda fuera por el filtro
 * de Magento, no por uno nuestro. Ver `Model\VersioningSchema`.
 *
 * `category_id` es siempre `entity_id` (identidad de negocio ESTABLE a
 * través de versiones), nunca la clave de paginación (`row_id` con
 * Staging): es el id que usan `derive_category_effect`,
 * `Category.magento_id` y `catalog_category_product` del lado Magento.
 *
 * `is_active`/`name` se leen con el mismo patrón global/override que
 * `ProductReader`: la fila con `store_id = N` sobreescribe a la de
 * `store_id = 0` si existe. A diferencia de `ProductReader` (que deja el
 * merge al ingestor Python), este lector YA resuelve el valor efectivo por
 * store view, porque el contrato de salida (`store_states`) es una lista
 * de estados YA resueltos, uno por CADA store view de la instancia —tenga
 * o no override— para que el lado Python nunca tenga que adivinar si una
 * ausencia significa "inactiva" o "desconocida".
 *
 * `store_states` es una LISTA de objetos, deliberadamente, no un mapa
 * store_id -> estado: un mapa con claves enteras consecutivas desde cero
 * (2 store views: 1 y 3... pero también instancias con solo la store view
 * 1, o con 0/1) puede colapsar a un array JSON del lado PHP, exactamente
 * el bug que ya afectó a las etiquetas de opción (Task A1, `json_encode`
 * sobre 45.801 de 50.545 opciones). Una lista de objetos no tiene ese
 * problema.
 *
 * En esta instancia ambas store views (PY, BR) cuelgan de la misma root
 * category (2): el caso que de verdad discrimina no es el árbol, es
 * `is_active` por store view — ver `CategoryReaderTest`.
 */
class CategoryReader implements CategoryReaderInterface
{
    private const MAX_LIMIT = 1000;

    private const CATEGORY_TABLE = 'catalog_category_entity';

    private const ENTITY_TYPE_CODE = 'catalog_category';

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly Cursor $cursor,
        // Inyectada, no instanciada con `new`: es la MISMA clase que
        // ProductReader usa para catalog_product_entity. Ver el docblock de
        // la clase para el porqué de reusarla también acá.
        private readonly EntityKeyResolver $entityKeyResolver,
        // Inyectado, no instanciado con `new`: la MISMA clase que usan
        // AttributeReader y EnvironmentProbe. Ver Model\EntityTypeResolver.
        private readonly EntityTypeResolver $entityTypeResolver,
        private readonly StoreManagerInterface $storeManager,
    ) {
    }

    public function getPage(int $limit = 500, ?string $cursor = null): array
    {
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $after = $this->cursor->decode($cursor);
        $connection = $this->resource->getConnection();
        $keyColumn = $this->entityKeyResolver->resolve(self::CATEGORY_TABLE);

        $select = $this->baseCategorySelect($connection, $keyColumn)
            ->where('e.' . $keyColumn . ' > ?', $after)
            ->order('e.' . $keyColumn . ' ASC')
            ->limit($limit);

        $rows = $connection->fetchAll($select);
        if ($rows === []) {
            return WebApiEnvelope::wrap(['items' => [], 'next_cursor' => null]);
        }

        $keys = array_map(static fn (array $row): int => (int) $row['key'], $rows);
        $storeIds = $this->storeViewIds();
        $nameValues = $this->attributeValues($connection, $keyColumn, $keys, 'name');
        $activeValues = $this->attributeValues($connection, $keyColumn, $keys, 'is_active');

        $items = [];
        foreach ($rows as $row) {
            $key = (int) $row['key'];
            $items[] = [
                'category_id' => (int) $row['category_id'],
                'path' => $this->pathToInts((string) $row['path']),
                'default_name' => (string) ($nameValues[$key][0] ?? ''),
                'store_states' => $this->storeStates(
                    $storeIds,
                    $activeValues[$key] ?? [],
                    $nameValues[$key] ?? []
                ),
            ];
        }

        return WebApiEnvelope::wrap([
            'items' => $items,
            'next_cursor' => count($rows) < $limit
                ? null
                : $this->cursor->encode($keys[array_key_last($keys)]),
        ]);
    }

    private function baseCategorySelect(AdapterInterface $connection, string $keyColumn): Select
    {
        $table = $this->resource->getTableName(self::CATEGORY_TABLE);

        return $connection->select()
            ->from(['e' => $table], [
                'key' => 'e.' . $keyColumn,
                // category_id de negocio: SIEMPRE entity_id, estable a
                // través de versiones, aunque la clave de paginación (con
                // Staging) sea row_id. Ver docblock de la clase.
                'category_id' => 'e.entity_id',
                'path' => 'e.path',
            ]);
    }

    /**
     * `catalog_category_entity.path` es "1/2/108/109": una lista de ids de
     * ancestros como string. `derive_category_effect` decide pertenencia
     * al árbol con `root_category_id in assignment_path`, así que esto
     * debe volver como lista de enteros, no como string a parsear del lado
     * Python.
     *
     * @return int[]
     */
    private function pathToInts(string $path): array
    {
        return array_values(array_map(
            static fn (string $segment): int => (int) $segment,
            array_filter(explode('/', $path), static fn (string $segment): bool => $segment !== '')
        ));
    }

    /**
     * Todas las filas de un atributo EAV de categoría ('name' o
     * 'is_active'), en TODOS los store_ids —nunca uno solo—: cuáles
     * store_ids tienen override solo se sabe leyendo todas las filas, y el
     * merge global/override se resuelve después, en storeStates().
     *
     * El `value` se preserva TAL CUAL, incluido NULL: una fila de override con
     * valor NULL no es un override falsy, es la AUSENCIA de valor en esa store
     * view, y `storeStates()` la hace heredar el global. Castearlo con
     * `(string)` la convertía en `''` y `(bool) (int) ''` en `false`, así que
     * la categoría se reportaba INACTIVA en esa tienda en vez de heredar — la
     * regla de presencia-no-verdad invertida respecto de
     * `ProductReader::eavValues()` y `SignalReader::scopedValue()`. Un `'0'`
     * sigue siendo un override real y sigue ganando.
     *
     * @param int[] $keys
     * @return array<int, array<int, string|null>> [key][store_id] => value
     */
    private function attributeValues(
        AdapterInterface $connection,
        string $keyColumn,
        array $keys,
        string $attributeCode
    ): array {
        $attribute = $this->categoryAttribute($connection, $attributeCode);
        $table = $this->resource->getTableName(self::CATEGORY_TABLE . '_' . $attribute['backend_type']);

        $select = $connection->select()
            ->from(['v' => $table], [
                'entity' => 'v.' . $keyColumn,
                'store_id' => 'v.store_id',
                'value' => 'v.value',
            ])
            ->where('v.attribute_id = ?', $attribute['attribute_id'])
            ->where('v.' . $keyColumn . ' IN (?)', $keys);

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(int) $row['entity']][(int) $row['store_id']] = $row['value'] === null
                ? null
                : (string) $row['value'];
        }

        return $out;
    }

    /**
     * Resuelve attribute_id y backend_type de un atributo de categoría en
     * runtime (nunca hardcodeado): un id fijo asumiría un orden de siembra
     * del EAV que ninguna instancia garantiza.
     *
     * @return array{attribute_id: int, backend_type: string}
     */
    private function categoryAttribute(AdapterInterface $connection, string $attributeCode): array
    {
        $entityTypeId = $this->entityTypeResolver->resolve(self::ENTITY_TYPE_CODE);
        $attributeTable = $this->resource->getTableName('eav_attribute');

        $select = $connection->select()
            ->from($attributeTable, ['attribute_id', 'backend_type'])
            ->where('entity_type_id = ?', $entityTypeId)
            ->where('attribute_code = ?', $attributeCode);

        $rows = $connection->fetchAll($select);
        if ($rows === []) {
            throw new LocalizedException(__(
                'no se encontró el atributo "%1" de categoría en eav_attribute',
                $attributeCode
            ));
        }

        $row = $rows[0];
        return ['attribute_id' => (int) $row['attribute_id'], 'backend_type' => (string) $row['backend_type']];
    }

    /**
     * Todas las store views de la instancia (nunca solo las que tengan
     * override): así el lado Python nunca tiene que adivinar si la
     * ausencia de una entrada significa "inactiva" o "desconocida".
     *
     * @return int[]
     */
    private function storeViewIds(): array
    {
        $ids = [];
        foreach ($this->storeManager->getStores() as $store) {
            $ids[] = (int) $store->getId();
        }
        return $ids;
    }

    /**
     * Resuelve is_active/name efectivos por store view con el mismo patrón
     * global/override que ProductReader: la fila de store_id=N gana si
     * existe; si no, se hereda la de store_id=0.
     *
     * Si ni siquiera existe la fila global (caso real verificado: la
     * categoría raíz absoluta de esta instancia no tiene fila de
     * is_active), se asume inactiva y nombre vacío en vez de adivinar un
     * valor: esa categoría nunca es el root_category_id de ninguna store
     * view real, así que no afecta ningún veredicto de
     * derive_category_effect.
     *
     * Una fila de override cuyo valor es NULL cuenta como ausente (el `??`
     * cae al global), no como un `false`/`''`: ver `attributeValues()`.
     *
     * @param int[] $storeIds
     * @param array<int, string|null> $activeByStore
     * @param array<int, string|null> $nameByStore
     * @return list<array{store_id: int, is_active: bool, name: string}>
     */
    private function storeStates(array $storeIds, array $activeByStore, array $nameByStore): array
    {
        $out = [];
        foreach ($storeIds as $storeId) {
            $activeValue = $activeByStore[$storeId] ?? $activeByStore[0] ?? null;
            $nameValue = $nameByStore[$storeId] ?? $nameByStore[0] ?? null;
            $out[] = [
                'store_id' => $storeId,
                'is_active' => $activeValue !== null && (bool) (int) $activeValue,
                'name' => (string) ($nameValue ?? ''),
            ];
        }
        return $out;
    }
}
