<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\WebApi;

use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\AttributeReader;
use Standard\Skudo\Model\CategoryReader;
use Standard\Skudo\Model\ChecksumReader;
use Standard\Skudo\Model\Cursor;
use Standard\Skudo\Model\DeltaReader;
use Standard\Skudo\Model\EntityKeyResolver;
use Standard\Skudo\Model\EntityTypeResolver;
use Standard\Skudo\Model\StoreViewGuard;
use Standard\Skudo\Model\EnvironmentProbe;
use Standard\Skudo\Model\ProductReader;
use Standard\Skudo\Model\SignalReader;
use Standard\Skudo\Model\VersioningSchema;

/**
 * El barrido de C1: TODO campo cuyo valor es un mapa (claves que dependen de
 * los datos) tiene que llegar al cliente como OBJETO JSON, también cuando el
 * mapa está vacío.
 *
 * Por qué existe este archivo. `json_encode()` de PHP emite `[]` —una lista
 * JSON— para un array vacío, y también para cualquier array cuyas claves
 * sean `0..n` consecutivas. Un mapa `código => valor` o `store_id => label`
 * es indistinguible de una lista para el serializador en cuanto se queda
 * vacío. Del otro lado del cable, el ingestor hace `.items()` sobre esos
 * campos y una lista de Python no tiene `.items()`.
 *
 * Este defecto se encontró TRES veces en el mismo proyecto, en tres campos
 * distintos, y las dos primeras se arreglaron una por una:
 *   1. `labels` de las opciones de atributo (Task A1). Se documentó en 25
 *      líneas de docblock y se arregló con `(object)`.
 *   2. `store_values`/`global_values` de producto (C1, sobre HTTP real).
 *      `full_sync` moría con `AttributeError: 'list' object has no attribute
 *      'items'` en el PRIMER producto sin override de store view, y los 105
 *      tests PHPUnit pasaban con el defecto puesto.
 * La lección no es "castear ese campo": es que la regla tiene que ser
 * MECÁNICA y cubrir el conjunto entero, o el cuarto campo con esta forma
 * repetirá la historia. De ahí las tres pruebas de abajo.
 *
 * El barrido completo, campo por campo, y por qué cada uno cae donde cae:
 *
 * | endpoint            | ruta                              | forma        |
 * |---------------------|-----------------------------------|--------------|
 * | /environment        | counts                            | registro fijo|
 * | /environment        | websites[*], store_groups[*], …    | registro fijo|
 * | /products           | items[*]                          | registro fijo|
 * | /products           | items[*].global_values             | **MAPA**     |
 * | /products           | items[*].store_values              | **MAPA**     |
 * | /products-by-sku    | items[*].global_values             | **MAPA**     |
 * | /products-by-sku    | items[*].store_values              | **MAPA**     |
 * | /deltas             | items[*]                          | registro fijo|
 * | /signals            | items[*]                          | registro fijo|
 * | /checksums          | (payload)                         | registro fijo|
 * | /attributes         | items[*]                          | registro fijo|
 * | /attributes         | items[*].options[*]               | registro fijo|
 * | /attributes         | items[*].options[*].labels        | **MAPA**     |
 * | /categories         | items[*]                          | registro fijo|
 * | /categories         | items[*].store_states[*]          | registro fijo|
 *
 * "Registro fijo" es un array asociativo cuyas claves son literales en el
 * código: nunca puede quedar vacío, así que nunca puede colapsar. "Mapa" es
 * un array cuyas claves salen de los datos: puede quedar vacío, y ahí es
 * donde `json_encode` lo convierte en lista. Los mapas se emiten con
 * `(object)`; los registros fijos, no.
 *
 * La clasificación no es decorativa: `testEveryObjectShapedFieldIsClassified`
 * recorre el payload REAL de los ocho endpoints y exige que cada nodo con
 * forma de objeto esté en una de las dos listas. Un campo nuevo con forma de
 * mapa que alguien agregue sin `(object)` no está en ninguna, así que hace
 * fallar la suite sin que nadie tenga que acordarse de esta prueba.
 */
class MapValuedFieldsAreJsonObjectsTest extends TestCase
{
    /**
     * Campos-mapa: claves que dependen de los datos. DEBEN llegar como
     * objeto JSON incluso vacíos.
     *
     * @return array<string, array{0: string, 1: string}>
     */
    public static function mapValuedFieldProvider(): array
    {
        return [
            '/products items[*].global_values' => ['products', 'items[*].global_values'],
            '/products items[*].store_values' => ['products', 'items[*].store_values'],
            '/products-by-sku items[*].global_values' => ['products_by_sku', 'items[*].global_values'],
            '/products-by-sku items[*].store_values' => ['products_by_sku', 'items[*].store_values'],
            '/attributes items[*].options[*].labels' => ['attributes', 'items[*].options[*].labels'],
        ];
    }

    /**
     * Registros de forma fija: array asociativo cuyas claves son literales
     * en el código, así que no puede quedar vacío ni colapsar a lista. No
     * llevan `(object)`.
     *
     * @return list<string>
     */
    private static function fixedShapeRecords(): array
    {
        return [
            'environment:',
            'environment:counts',
            'environment:websites[*]',
            'environment:store_groups[*]',
            'environment:store_views[*]',
            'products:',
            'products:items[*]',
            'products_by_sku:',
            'products_by_sku:items[*]',
            'deltas:',
            'deltas:items[*]',
            'signals:',
            'signals:items[*]',
            'checksums:',
            'attributes:',
            'attributes:items[*]',
            'attributes:items[*].options[*]',
            'categories:',
            'categories:items[*]',
            'categories:items[*].store_states[*]',
        ];
    }

    /**
     * Con datos: el campo existe y su tipo JSON es objeto. Discrimina el
     * caso "claves 0 y 1 consecutivas", que es el otro modo de colapso —un
     * mapa `{0: 'Cero', 1: 'Uno'}` sin `(object)` se serializa como
     * `["Cero","Uno"]`, con los datos intactos y la forma destruida.
     *
     * @dataProvider mapValuedFieldProvider
     */
    public function testAMapValuedFieldIsAJsonObjectWhenItHasContent(string $endpoint, string $path): void
    {
        $found = $this->valuesAt($this->encode($this->invoke($endpoint, populated: true)), $path);

        $this->assertNotSame(
            [],
            $found,
            "{$endpoint}:{$path} no apareció en el payload con datos: la prueba sería vacua"
        );

        foreach ($found as $value) {
            $this->assertIsObject(
                $value,
                "{$endpoint}:{$path} llegó como " . get_debug_type($value) . ' y no como objeto JSON'
            );
        }
    }

    /**
     * Vacío: el caso que rompió `full_sync` de verdad. Sin `(object)`, un
     * mapa vacío llega como `[]` y el ingestor muere con
     * `AttributeError: 'list' object has no attribute 'items'`.
     *
     * @dataProvider mapValuedFieldProvider
     */
    public function testAMapValuedFieldIsAnEmptyJsonObjectAndNeverAnEmptyArray(
        string $endpoint,
        string $path
    ): void {
        $found = $this->valuesAt($this->encode($this->invoke($endpoint, populated: false)), $path);

        $this->assertNotSame(
            [],
            $found,
            "{$endpoint}:{$path} no apareció en el payload sin overrides: la prueba sería vacua"
        );

        foreach ($found as $value) {
            $this->assertIsObject(
                $value,
                "{$endpoint}:{$path} vacío llegó como " . get_debug_type($value)
                . ": json_encode() emite `[]` para un array PHP vacío, indistinguible de una "
                . 'lista, y el ingestor hace .items() sobre este campo. Falta el (object).'
            );
            $this->assertSame(
                '{}',
                json_encode($value),
                "{$endpoint}:{$path} vacío no serializa como `{}`"
            );
        }
    }

    /**
     * La guarda que hace mecánico el barrido. Recorre el payload REAL de los
     * ocho endpoints y exige que TODO nodo con forma de objeto esté
     * clasificado: o es un campo-mapa declarado (y entonces ya lo cubren las
     * dos pruebas de arriba), o es un registro de forma fija declarado.
     *
     * Un campo nuevo con forma de mapa cae en ninguna de las dos listas y
     * esta prueba falla nombrando su ruta. Es lo que faltaba las tres veces
     * que este defecto apareció: la regla existía, escrita en un docblock,
     * y no había nada que la aplicara al conjunto.
     */
    public function testEveryObjectShapedFieldIsClassified(): void
    {
        $declaredMaps = [];
        foreach (self::mapValuedFieldProvider() as [$endpoint, $path]) {
            $declaredMaps[] = $endpoint . ':' . $path;
        }
        $known = array_merge($declaredMaps, self::fixedShapeRecords());

        $unclassified = [];
        $seen = [];
        foreach (self::endpoints() as $endpoint) {
            foreach ([true, false] as $populated) {
                $payload = $this->encode($this->invoke($endpoint, populated: $populated));
                foreach ($this->objectPaths($payload) as $path) {
                    $qualified = $endpoint . ':' . $path;
                    $seen[$qualified] = true;
                    if (!in_array($qualified, $known, true)) {
                        $unclassified[$qualified] = true;
                    }
                }
            }
        }

        $this->assertSame(
            [],
            array_keys($unclassified),
            'hay campos con forma de objeto JSON sin clasificar. Si el campo es un MAPA '
            . '(claves que dependen de los datos) tiene que emitirse con (object) y sumarse a '
            . 'mapValuedFieldProvider(); si es un registro de forma fija (claves literales en '
            . 'el código, nunca vacío) va en fixedShapeRecords(). Un mapa sin (object) llega '
            . 'como `[]` al ingestor en cuanto se queda vacío.'
        );

        // Contra-guarda: las rutas declaradas tienen que existir de verdad.
        // Una lista con entradas muertas dejaría de proteger sin avisar.
        $this->assertSame(
            [],
            array_values(array_diff($known, array_keys($seen))),
            'hay rutas declaradas que no aparecen en ningún payload: la lista quedó desfasada '
            . 'del código y ya no protege lo que dice proteger'
        );
    }

    /** @return list<string> */
    private static function endpoints(): array
    {
        return [
            'environment', 'products', 'products_by_sku', 'deltas',
            'signals', 'checksums', 'attributes', 'categories',
        ];
    }

    // --- recorrido de rutas ------------------------------------------------

    /**
     * Rutas de todo nodo con forma de objeto JSON, en la notación
     * `items[*].options[*].labels`. La raíz (el payload) es la cadena vacía.
     *
     * @return list<string>
     */
    private function objectPaths(mixed $node, string $path = ''): array
    {
        if (is_object($node)) {
            $out = [$path];
            foreach (get_object_vars($node) as $key => $value) {
                $out = array_merge($out, $this->objectPaths($value, $this->join($path, (string) $key)));
            }
            return $out;
        }

        if (is_array($node)) {
            $out = [];
            foreach ($node as $value) {
                $out = array_merge($out, $this->objectPaths($value, $path . '[*]'));
            }
            return array_values(array_unique($out));
        }

        return [];
    }

    private function join(string $path, string $key): string
    {
        return $path === '' ? $key : $path . '.' . $key;
    }

    /**
     * Todos los valores en una ruta con comodines `[*]`.
     *
     * @return list<mixed>
     */
    private function valuesAt(mixed $node, string $path): array
    {
        if ($path === '') {
            return [$node];
        }

        $out = [];
        foreach ($this->objectPathsWithValues($node) as [$candidate, $value]) {
            if ($candidate === $path) {
                $out[] = $value;
            }
        }
        return $out;
    }

    /**
     * @return list<array{0: string, 1: mixed}>
     */
    private function objectPathsWithValues(mixed $node, string $path = ''): array
    {
        if (is_object($node)) {
            $out = [[$path, $node]];
            foreach (get_object_vars($node) as $key => $value) {
                $out = array_merge(
                    $out,
                    $this->objectPathsWithValues($value, $this->join($path, (string) $key))
                );
            }
            return $out;
        }

        if (is_array($node)) {
            $out = [];
            foreach ($node as $value) {
                $out = array_merge($out, $this->objectPathsWithValues($value, $path . '[*]'));
            }
            return $out;
        }

        return [[$path, $node]];
    }

    /**
     * El paso que importa: lo que se inspecciona es el resultado de
     * `json_encode` + `json_decode`, es decir la forma que el CLIENTE
     * recibe, no la que el modelo devuelve. Sin este viaje de ida y vuelta,
     * un array PHP asociativo y un stdClass son indistinguibles para un
     * `assertIsArray`, que es justo cómo el defecto sobrevivió.
     */
    private function encode(mixed $returned): mixed
    {
        $json = json_encode($returned);
        $this->assertIsString($json, 'el payload no es serializable a JSON');

        // El envoltorio de WebApiEnvelope: `[<payload>]`.
        $decoded = json_decode($json, false);
        $this->assertIsArray($decoded);
        $this->assertCount(1, $decoded);

        return $decoded[0];
    }

    // --- invocación de los modelos de producción --------------------------

    private function invoke(string $endpoint, bool $populated): mixed
    {
        $resource = $this->resource($populated);

        return match ($endpoint) {
            'environment' => $this->environmentProbe($resource)->getProfile(),
            'products' => $this->productReader($resource)->getPage(storeId: 1, limit: 10),
            'products_by_sku' => $this->productReader($resource)->getBySku(1, ['SKU-A']),
            'deltas' => (new DeltaReader($resource, new VersioningSchema($resource)))->getChanges(0, 10),
            'signals' => $this->signalReader($resource)->getSignals(1, 90),
            'checksums' => (new ChecksumReader($resource, $this->storeViewGuard()))->getChecksums(1),
            'attributes' => (new AttributeReader($resource, new Cursor(), new EntityTypeResolver($resource)))
                ->getPage(10),
            'categories' => $this->categoryReader($resource)->getPage(10),
        };
    }

    private function productReader(ResourceConnection $resource): ProductReader
    {
        return new ProductReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource),
            $this->storeViewGuard()
        );
    }

    private function signalReader(ResourceConnection $resource): SignalReader
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(true);

        return new SignalReader(
            $resource,
            $modules,
            new EntityKeyResolver($resource),
            $this->storeViewGuard()
        );
    }

    private function storeViewGuard(): StoreViewGuard
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStore')->willReturn($this->createMock(\Magento\Store\Model\Store::class));

        return new StoreViewGuard($storeManager);
    }

    private function categoryReader(ResourceConnection $resource): CategoryReader
    {
        return new CategoryReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource),
            new EntityTypeResolver($resource),
            $this->storeManager()
        );
    }

    private function environmentProbe(ResourceConnection $resource): EnvironmentProbe
    {
        $metadata = $this->createMock(ProductMetadataInterface::class);
        $metadata->method('getEdition')->willReturn('Enterprise');
        $metadata->method('getVersion')->willReturn('2.4.8-p3');

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(true);

        return new EnvironmentProbe(
            $metadata,
            $modules,
            $resource,
            $this->storeManager(),
            new EntityKeyResolver($resource),
            new EntityTypeResolver($resource)
        );
    }

    private function storeManager(): StoreManagerInterface
    {
        // `Store` real sin constructor, no un mock de la interfaz:
        // EnvironmentProbe::storeViews() llama a isActive()/getConfig()/
        // getCurrentCurrencyCode(), que StoreInterface no declara.
        $store = $this->createMock(\Magento\Store\Model\Store::class);
        $store->method('getId')->willReturn(1);
        $store->method('getStoreGroupId')->willReturn(1);
        $store->method('getCode')->willReturn('py');
        $store->method('getName')->willReturn('Paraguay');
        $store->method('isActive')->willReturn(true);
        $store->method('getConfig')->willReturn('es_AR');
        $store->method('getCurrentCurrencyCode')->willReturn('PYG');

        $website = new \Magento\Framework\DataObject(['id' => 1, 'code' => 'base', 'name' => 'Website Paraguay']);
        $group = new \Magento\Framework\DataObject([
            'id' => 1, 'website_id' => 1, 'code' => 'main', 'name' => 'Casa Nissei', 'root_category_id' => 2,
        ]);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getWebsites')->willReturn([$website]);
        $storeManager->method('getGroups')->willReturn([$group]);
        $storeManager->method('getStores')->willReturn([$store]);

        return $storeManager;
    }

    /**
     * Fixtures por tabla. `$populated` decide si los MAPAS traen contenido
     * o quedan vacíos; el resto de las filas está siempre, para que en los
     * dos casos el payload llegue a tener items (si no hubiera items, las
     * rutas no existirían y las pruebas serían vacuas — de ahí los
     * `assertNotSame([], $found)`).
     */
    private function resource(bool $populated): ResourceConnection
    {
        $rowsByTable = [
            'catalog_product_entity' => [[
                'key' => 1, 'sku' => 'SKU-A', 'attribute_set_id' => 4, 'type_id' => 'simple',
                'updated_at' => '2026-01-01 00:00:00', 'created_in' => 1, 'updated_in' => 2_147_483_647,
                // Para la consulta de margen de SignalReader, que también
                // hace FROM de esta tabla.
                'code' => 'price', 'store_id' => 0, 'value' => '10.0000',
            ]],
            'catalog_product_website' => [['website_id' => 1, 'key' => 1]],
            'catalog_category_product' => [['category_id' => 10, 'sku' => 'SKU-A']],
            'standard_skudo_change_log' => [[
                'change_id' => 1, 'sku' => 'SKU-A', 'event' => 'save',
                'changed_at' => '2026-01-01 00:00:00',
            ]],
            'sales_order_item' => [[
                'sku' => 'SKU-A', 'units_sold' => '2', 'revenue' => '20.0000', 'revenue_missing' => '0',
            ]],
            'cataloginventory_stock_item' => [['qty' => '5.0000', 'sku' => 'SKU-A']],
            'eav_attribute' => [[
                'attribute_id' => 1, 'code' => 'marca', 'label' => 'Marca', 'frontend_input' => 'select',
                'is_required' => '0', 'is_global' => '1', 'is_filterable' => '1',
                // CategoryReader::categoryAttribute() lee de esta tabla.
                'backend_type' => 'varchar',
            ]],
            'eav_entity_attribute' => [['attribute_id' => 1, 'attribute_set_id' => 4]],
            'eav_attribute_option' => [['option_id' => 900_001, 'attribute_id' => 1]],
            'catalog_category_entity' => [[
                'key' => 501, 'category_id' => 1, 'path' => '1/2/10',
                'created_in' => 1, 'updated_in' => 2_147_483_647,
            ]],
            'catalog_category_entity_varchar' => [['entity' => 501, 'store_id' => 0, 'value' => 'Electro']],
        ];

        if ($populated) {
            // Los MAPAS con contenido. Las claves 0 y 1 consecutivas del mapa
            // de labels son deliberadas: es el otro modo de colapso.
            foreach (['varchar', 'int', 'decimal', 'text', 'datetime'] as $type) {
                $rowsByTable['catalog_product_entity_' . $type] = [
                    ['entity' => 1, 'code' => 'name_' . $type, 'value' => 'v'],
                ];
            }
            $rowsByTable['eav_attribute_option_value'] = [
                ['option_id' => 900_001, 'store_id' => 0, 'value' => 'Cero'],
                ['option_id' => 900_001, 'store_id' => 1, 'value' => 'Uno'],
            ];
        } else {
            // Los MAPAS vacíos: ni una fila EAV de producto, ni una etiqueta
            // de opción. Es el producto sin override de store view (C1) y la
            // opción sin ninguna etiqueta.
            $rowsByTable['eav_attribute_option_value'] = [];
        }

        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(
            static fn (): FixtureSelect => new FixtureSelect()
        );
        $connection->method('tableColumnExists')->willReturn(true);
        $connection->method('isTableExists')->willReturn(true);
        $connection->method('fetchAll')->willReturnCallback(
            static fn (FixtureSelect $select): array => $rowsByTable[$select->table] ?? []
        );
        $connection->method('fetchCol')->willReturnCallback(
            static fn (FixtureSelect $select): array => $select->table === 'catalog_product_entity'
                ? ['SKU-A']
                : ($select->table === 'inventory_stock_sales_channel' ? ['2'] : [])
        );
        $connection->method('fetchOne')->willReturnCallback(
            static fn (FixtureSelect $select): mixed => match ($select->table) {
                'store' => '1',
                'store_website' => 'base',
                'inventory_stock_sales_channel' => '2',
                'search_query' => '3',
                default => '4',
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return $resource;
    }
}

/**
 * Doble de `Select` que sólo recuerda de qué tabla se hizo FROM: es lo único
 * que estas pruebas necesitan para devolver las filas de fixture. No evalúa
 * condiciones — lo que se mide acá es la FORMA del payload serializado, no
 * qué filas devuelve un WHERE.
 */
final class FixtureSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    public function __construct()
    {
    }

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        $this->table = (string) array_values((array) $tables)[0];
        return $this;
    }

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function join($tables, $cond, $columns = '*', $schema = null): self
    {
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        return $this;
    }

    public function order($spec): self
    {
        return $this;
    }

    public function group($spec): self
    {
        return $this;
    }

    public function limit($count = null, $offset = null): self
    {
        return $this;
    }
}
