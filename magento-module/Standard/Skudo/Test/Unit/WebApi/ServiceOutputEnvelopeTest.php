<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\WebApi;

use Magento\Framework\Api\AttributeTypeResolverInterface;
use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\Cache\FrontendInterface;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\DB\Select;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Framework\Reflection\DataObjectProcessor;
use Magento\Framework\Reflection\FieldNamer;
use Magento\Framework\Reflection\MethodsMap;
use Magento\Framework\Reflection\TypeProcessor;
use Magento\Framework\Serialize\Serializer\Json;
use Magento\Framework\Webapi\ServiceOutputProcessor;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Api\AttributeReaderInterface;
use Standard\Skudo\Api\CategoryReaderInterface;
use Standard\Skudo\Api\ChecksumReaderInterface;
use Standard\Skudo\Api\DeltaReaderInterface;
use Standard\Skudo\Api\EnvironmentProbeInterface;
use Standard\Skudo\Api\ProductReaderInterface;
use Standard\Skudo\Api\SignalReaderInterface;
use Standard\Skudo\Model\VersioningSchema;
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

/**
 * B1 — la prueba que faltaba: afirma la forma que el CLIENTE recibe, no la
 * que el modelo devuelve.
 *
 * Magento no serializa lo que un método de web API devuelve: lo pasa antes
 * por `Magento\Framework\Webapi\ServiceOutputProcessor::process()`, que mira
 * el `@return` del docblock de la INTERFAZ y, para `mixed[]`, hace
 *
 *     foreach ($data as $datum) { $result[] = $datum; }
 *
 * es decir, REINDEXA el primer nivel y descarta sus claves. Un método que
 * devuelve `['items' => [...], 'next_cursor' => 'x']` llega al cliente como
 * `[[...], 'x']`: `items` y `next_cursor` dejan de existir sobre el cable.
 *
 * Todas las pruebas de los dos lados —las de Model/ de acá y las de Python—
 * afirmaban la forma ANTERIOR a esa conversión (la que devuelve el modelo, o
 * la que el mock entrega), así que el fallo sobrevivió doce revisiones sin
 * que nada rojo lo señalara. Esta prueba cierra ese hueco: construye el
 * ServiceOutputProcessor REAL del vendor de la instalación (solo lectura, sin
 * bootstrap de Magento) y afirma que el valor de vuelta de CADA endpoint pasa
 * por él sin perder nada.
 *
 * La convención que lo consigue es envolver el payload un nivel
 * (`WebApiEnvelope::wrap()`, `return [$payload]`): el `foreach` reindexa una
 * lista de un solo elemento a la misma lista de un solo elemento, y el
 * payload —que es el elemento, no el contenedor— llega intacto. El lado
 * Python desenvuelve con `response.json()[0]`.
 *
 * Si alguien revierte el envoltorio en cualquiera de los ocho endpoints,
 * `assertSame($returned, $processed)` falla para ese endpoint: el modelo
 * devolvería un mapa y el procesador entregaría una lista sin claves.
 */
class ServiceOutputEnvelopeTest extends TestCase
{
    /**
     * Cada entrada es un endpoint real de `etc/webapi.xml`: la interfaz y el
     * metodo que la ruta declara, mas la clave con la que `invoke()` llama al
     * modelo de produccion que los implementa. No se inventan payloads: lo
     * que se procesa es lo que el modelo DEVUELVE de verdad.
     *
     * @return array<string, array{0: string, 1: string, 2: string}>
     */
    public static function endpointProvider(): array
    {
        return [
            'GET /environment' => [EnvironmentProbeInterface::class, 'getProfile', 'environment'],
            'GET /products' => [ProductReaderInterface::class, 'getPage', 'products'],
            'POST /products-by-sku' => [ProductReaderInterface::class, 'getBySku', 'products_by_sku'],
            'GET /deltas' => [DeltaReaderInterface::class, 'getChanges', 'deltas'],
            'GET /signals' => [SignalReaderInterface::class, 'getSignals', 'signals'],
            'GET /checksums' => [ChecksumReaderInterface::class, 'getChecksums', 'checksums'],
            'GET /attributes' => [AttributeReaderInterface::class, 'getPage', 'attributes'],
            'GET /categories' => [CategoryReaderInterface::class, 'getPage', 'categories'],
        ];
    }

    /**
     * Llama al modelo de produccion detras de cada ruta y devuelve, tal cual,
     * lo que ese metodo retorna.
     */
    private function invoke(string $key): mixed
    {
        return match ($key) {
            'environment' => $this->environmentProbe()->getProfile(),
            'products' => $this->productReader()->getPage(storeId: 1, limit: 10),
            'products_by_sku' => $this->productReader()->getBySku(1, []),
            'deltas' => $this->deltaReader()->getChanges(0, 10),
            'signals' => $this->signalReader()->getSignals(1, 90),
            'checksums' => $this->checksumReader()->getChecksums(1),
            'attributes' => $this->attributeReader()->getPage(10),
            'categories' => $this->categoryReader()->getPage(10),
        };
    }

    /**
     * @dataProvider endpointProvider
     */
    public function testTheCallerReceivesTheSameStructureTheEndpointReturned(
        string $interface,
        string $method,
        string $key
    ): void {
        $returned = $this->invoke($key);

        $processed = $this->serviceOutputProcessor()->process($returned, $interface, $method);

        $this->assertSame(
            $returned,
            $processed,
            "{$interface}::{$method} pierde forma al pasar por ServiceOutputProcessor: "
            . 'el payload debe viajar envuelto un nivel (WebApiEnvelope::wrap) para que '
            . 'el foreach de convertValue() no descarte sus claves de primer nivel'
        );
        $this->assertCount(1, $processed, 'el envoltorio es exactamente un nivel');
        $this->assertIsArray($processed[0]);
        $this->assertNotSame(
            [],
            array_filter(array_keys($processed[0]), 'is_string'),
            'el payload envuelto debe conservar sus claves de primer nivel'
        );
    }

    /**
     * Control negativo, y la razón por la que existe el envoltorio: el MISMO
     * procesador, sobre el MISMO payload sin envolver, descarta las claves.
     *
     * Sin esta prueba, la de arriba podría pasar por un procesador que no
     * hace nada; con ella queda demostrado que sí transforma, y que el
     * envoltorio es lo único que lo neutraliza. Si Magento cambiara alguna
     * vez ese `foreach`, esta prueba lo avisa en vez de dejar la convención
     * sin explicación.
     */
    public function testWithoutTheEnvelopeTheTopLevelKeysAreDiscarded(): void
    {
        $bare = ['items' => [['option_id' => 216]], 'next_cursor' => 'skudo1:5'];

        $processed = $this->serviceOutputProcessor()->process(
            $bare,
            AttributeReaderInterface::class,
            'getPage'
        );

        $this->assertSame([[['option_id' => 216]], 'skudo1:5'], $processed);
        $this->assertArrayNotHasKey('items', $processed);
        $this->assertArrayNotHasKey('next_cursor', $processed);
    }

    /**
     * El `(object)` de las etiquetas de opción (Task A1) sigue siendo
     * correcto y NO debe quitarse: `convertValue()` solo recorre el primer
     * nivel, así que todo lo anidado —incluido un stdClass— viaja intacto y
     * `json_encode` lo emite como objeto JSON, que es lo que
     * `_labels_by_store_id` del lado Python exige.
     */
    public function testNestedOptionLabelsSurviveAsAnObject(): void
    {
        $payload = [
            'items' => [
                ['option_id' => 216, 'labels' => (object) ['0' => 'Exchange', '1' => 'Cambio']],
            ],
            'next_cursor' => null,
        ];

        $processed = $this->serviceOutputProcessor()->process(
            [$payload],
            AttributeReaderInterface::class,
            'getPage'
        );

        $this->assertSame(
            '[{"items":[{"option_id":216,"labels":{"0":"Exchange","1":"Cambio"}}],"next_cursor":null}]',
            json_encode($processed)
        );
    }

    /**
     * Comprobación permanente: toda ruta declarada en `etc/webapi.xml` tiene
     * que estar cubierta por el provider de arriba. Un endpoint nuevo que se
     * agregue sin envoltorio (el fallo B1, repetido) hace fallar ESTA prueba
     * aunque nadie se acuerde de sumar su caso.
     */
    public function testEveryRouteDeclaredInWebapiXmlIsCovered(): void
    {
        $xml = simplexml_load_file(__DIR__ . '/../../../etc/webapi.xml');
        $this->assertNotFalse($xml, 'no se pudo leer etc/webapi.xml');

        $declared = [];
        foreach ($xml->route as $route) {
            $service = $route->service;
            $declared[] = ((string) $service['class']) . '::' . ((string) $service['method']);
        }
        sort($declared);

        $covered = [];
        foreach (self::endpointProvider() as [$interface, $method, $_key]) {
            $covered[] = $interface . '::' . $method;
        }
        $covered = array_values(array_unique($covered));
        sort($covered);

        $this->assertSame(
            $declared,
            $covered,
            'hay rutas de webapi.xml sin caso en endpointProvider(): un endpoint sin '
            . 'esta prueba puede devolver un payload sin envolver y romperse solo sobre HTTP real'
        );
    }

    /**
     * El ServiceOutputProcessor REAL del vendor de la instalación, armado sin
     * ObjectManager ni bootstrap de Magento: `MethodsMap` lee el docblock de
     * nuestra propia interfaz por reflexión (de ahí que el `@return mixed[]`
     * que gobierna todo esto sea el de verdad, no una constante copiada), con
     * un caché en memoria que nunca acierta y el serializador JSON inyectado
     * por reflexión —único punto donde la clase real recurriría al
     * ObjectManager.
     */
    private function serviceOutputProcessor(): ServiceOutputProcessor
    {
        $cache = new class implements FrontendInterface {
            public function test($identifier)
            {
                return false;
            }

            public function load($identifier)
            {
                return false;
            }

            public function save($data, $identifier, array $tags = [], $lifeTime = null)
            {
                return true;
            }

            public function remove($identifier)
            {
                return true;
            }

            public function clean($mode = 'all', array $tags = [])
            {
                return true;
            }

            public function getBackend()
            {
                return null;
            }

            public function getLowLevelFrontend()
            {
                return null;
            }
        };

        $typeProcessor = new TypeProcessor();
        $typeResolver = $this->createMock(AttributeTypeResolverInterface::class);
        $methodsMap = new MethodsMap($cache, $typeProcessor, $typeResolver, new FieldNamer());

        $serializer = new \ReflectionProperty(MethodsMap::class, 'serializer');
        $serializer->setAccessible(true);
        $serializer->setValue($methodsMap, new Json());

        // Nunca se usa: convertValue() solo llama al DataObjectProcessor para
        // elementos que son OBJETOS de primer nivel, y ningún endpoint
        // devuelve uno. Se construye sin constructor para no arrastrar su
        // grafo de dependencias a una prueba unitaria.
        $dataObjectProcessor = (new \ReflectionClass(DataObjectProcessor::class))
            ->newInstanceWithoutConstructor();

        return new ServiceOutputProcessor($dataObjectProcessor, $methodsMap, $typeProcessor);
    }

    // --- modelos de producción con dobles mínimos -------------------------
    //
    // El payload puede ser vacío: lo que esta prueba mide es la FORMA del
    // contenedor, y `['items' => [], 'next_cursor' => null]` sin envolver ya
    // se convierte en `[[], null]` — dos elementos, sin claves. Es
    // discriminante con el catálogo vacío.

    private function resourceReturning(AdapterInterface $connection): ResourceConnection
    {
        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return $resource;
    }

    private function emptyConnection(): AdapterInterface
    {
        $connection = $this->createMock(AdapterInterface::class);
        $select = $this->createMock(Select::class);
        $select->method('from')->willReturnSelf();
        $select->method('join')->willReturnSelf();
        $select->method('where')->willReturnSelf();
        $select->method('order')->willReturnSelf();
        $select->method('group')->willReturnSelf();
        $select->method('limit')->willReturnSelf();

        $connection->method('select')->willReturn($select);
        $connection->method('fetchAll')->willReturn([]);
        $connection->method('fetchCol')->willReturn([]);
        // entity_type_id de catalog_product: AttributeReader lo resuelve en
        // runtime y aborta si no lo encuentra.
        $connection->method('fetchOne')->willReturn('4');
        $connection->method('tableColumnExists')->willReturn(false);
        $connection->method('isTableExists')->willReturn(false);

        return $connection;
    }

    private function productReader(): ProductReader
    {
        $resource = $this->resourceReturning($this->emptyConnection());

        return new ProductReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource),
            $this->storeViewGuard()
        );
    }

    private function deltaReader(): DeltaReader
    {
        $resource = $this->resourceReturning($this->emptyConnection());

        return new DeltaReader($resource, new VersioningSchema($resource));
    }

    private function checksumReader(): ChecksumReader
    {
        $resource = $this->resourceReturning($this->emptyConnection());

        return new ChecksumReader($resource, $this->storeViewGuard());
    }

    private function attributeReader(): AttributeReader
    {
        $resource = $this->resourceReturning($this->emptyConnection());

        return new AttributeReader($resource, new Cursor(), new EntityTypeResolver($resource));
    }

    private function categoryReader(): CategoryReader
    {
        $resource = $this->resourceReturning($this->emptyConnection());
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStores')->willReturn([]);

        return new CategoryReader(
            $resource,
            new Cursor(),
            new EntityKeyResolver($resource),
            new EntityTypeResolver($resource),
            $storeManager
        );
    }

    private function signalReader(): SignalReader
    {
        $resource = $this->resourceReturning($this->emptyConnection());
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(false);

        return new SignalReader(
            $resource,
            $modules,
            new EntityKeyResolver($resource),
            $this->storeViewGuard()
        );
    }

    /**
     * M3: un guard con un StoreManager que conoce cualquier store view. Lo
     * que esta prueba mide es la FORMA del envoltorio, no la validación del
     * storeId (eso es StoreViewGuardTest).
     */
    private function storeViewGuard(): StoreViewGuard
    {
        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getStore')->willReturn($this->createMock(\Magento\Store\Model\Store::class));

        return new StoreViewGuard($storeManager);
    }

    private function environmentProbe(): EnvironmentProbe
    {
        $resource = $this->resourceReturning($this->emptyConnection());
        $metadata = $this->createMock(ProductMetadataInterface::class);
        $metadata->method('getEdition')->willReturn('Community');
        $metadata->method('getVersion')->willReturn('2.4.8-p3');

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturn(false);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getWebsites')->willReturn([]);
        $storeManager->method('getGroups')->willReturn([]);
        $storeManager->method('getStores')->willReturn([]);

        return new EnvironmentProbe(
            $metadata,
            $modules,
            $resource,
            $storeManager,
            new EntityKeyResolver($resource),
            new EntityTypeResolver($resource)
        );
    }
}
