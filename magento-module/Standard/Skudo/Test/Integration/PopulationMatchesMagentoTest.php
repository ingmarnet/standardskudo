<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Integration;

use Magento\Framework\App\Bootstrap;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\App\State;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Api\ChecksumReaderInterface;
use Standard\Skudo\Api\DeltaReaderInterface;
use Standard\Skudo\Api\EnvironmentProbeInterface;
use Standard\Skudo\Api\ProductReaderInterface;
use Standard\Skudo\Model\ContentDigest;
use Standard\Skudo\Model\VersioningSchema;

/**
 * La prueba que HABRÍA detectado C2, y que ninguna unitaria puede hacer.
 *
 * C2 —el módulo componiendo su propia ventana de versión activa con la que
 * Magento inyecta— se escondió doce revisiones porque todas las pruebas del
 * lado PHP usan un doble de `Select` que, por construcción, no sabe nada del
 * renderer de la parte FROM del framework. Un doble así no puede mostrar la
 * conjunción de dos filtros cuando sólo conoce uno. La única forma de
 * afirmar la propiedad que importa —"la población que el módulo reporta es
 * exactamente la que ESTA instancia de Magento considera activa"— es
 * preguntárselo a una instancia real.
 *
 * Se salta sola si no hay una: necesita `SKUDO_MAGENTO_APP_ROOT` apuntando a
 * una instalación con `app/etc/env.php` y base de datos accesible. En el
 * entorno de desarrollo de este proyecto:
 *
 *     SKUDO_MAGENTO_ROOT=/home/ingmar/magento-skudo-dev \
 *     SKUDO_MAGENTO_APP_ROOT=/home/ingmar/magento-skudo-dev \
 *       vendor/bin/phpunit -c phpunit.xml
 *
 * No referencia ninguna clase de `Magento\Staging\*` ni ramifica por
 * edición: construye un `Select` normal del framework y compara. En una
 * instancia Community (sin columnas de versión) la comparación sigue siendo
 * válida y trivialmente verdadera, que es exactamente lo que se quiere
 * afirmar ahí.
 */
class PopulationMatchesMagentoTest extends TestCase
{
    /**
     * Tope de seguridad: esta prueba pagina el catálogo entero. Contra una
     * instancia de producción de cientos de miles de productos eso no es una
     * prueba, es un barrido; se salta y lo dice.
     */
    private const MAX_CATALOG = 5000;

    private static ?\Magento\Framework\ObjectManagerInterface $objectManager = null;

    public static function setUpBeforeClass(): void
    {
        $root = getenv('SKUDO_MAGENTO_APP_ROOT');
        if ($root === false || $root === '' || !is_file($root . '/app/etc/env.php')) {
            return;
        }

        require_once $root . '/app/bootstrap.php';
        $bootstrap = Bootstrap::create($root, []);
        self::$objectManager = $bootstrap->getObjectManager();
        self::$objectManager->get(State::class)->setAreaCode('webapi_rest');
    }

    protected function setUp(): void
    {
        if (self::$objectManager === null) {
            $this->markTestSkipped(
                'sin instancia: definí SKUDO_MAGENTO_APP_ROOT apuntando a una instalación '
                . 'de Magento con app/etc/env.php para ejercitar esta prueba'
            );
        }
    }

    /**
     * El invariante de C2: `/products` devuelve exactamente las filas que un
     * `Select` normal del framework sobre `catalog_product_entity` devuelve.
     * Ni una más (versiones que Magento no sirve) ni una menos (filas que un
     * filtro propio del módulo descartaría).
     *
     * Si alguien vuelve a agregar `created_in <= UNIX_TIMESTAMP()` en
     * cualquiera de los lectores, esta prueba falla en cuanto la instancia
     * tenga una sola fila en la zona de divergencia entre el reloj y el id de
     * la versión aplicada.
     */
    public function testProductsReturnsExactlyThePopulationMagentoConsidersActive(): void
    {
        $expected = $this->magentoActiveSkus();

        $actual = [];
        $cursor = null;
        do {
            $page = self::$objectManager->get(ProductReaderInterface::class)
                ->getPage(storeId: $this->aStoreId(), limit: 1000, cursor: $cursor)[0];
            foreach ($page['items'] as $item) {
                $actual[] = (string) $item['sku'];
            }
            $cursor = $page['next_cursor'];
        } while ($cursor !== null);

        sort($expected, SORT_STRING);
        sort($actual, SORT_STRING);

        $this->assertSame(
            $expected,
            $actual,
            'la población de /products no coincide con la que Magento considera activa: '
            . 'un filtro de versión propio del módulo la estrecha (C2), o falta paginar'
        );
    }

    /**
     * M1: `/checksums.product_count` y `/environment.counts.products` cuentan
     * la MISMA población que `/products`. Cuando no la cuentan, la diferencia
     * se lee como deriva del espejo y `reconcile()` pide un re-sync completo
     * que no puede arreglar nada.
     */
    public function testChecksumsAndEnvironmentCountTheSamePopulationAsProducts(): void
    {
        $expected = count($this->magentoActiveSkus());

        $checksums = self::$objectManager->get(ChecksumReaderInterface::class)
            ->getChecksums($this->aStoreId())[0];
        $environment = self::$objectManager->get(EnvironmentProbeInterface::class)->getProfile()[0];

        $this->assertSame($expected, $checksums['product_count'], '/checksums cuenta otra población');
        $this->assertSame(
            $expected,
            $environment['counts']['products'],
            '/environment.counts.products cuenta otra población'
        );
    }

    /**
     * H1: el digest de contenido por partición cubre EXACTAMENTE la población
     * activa, y cada SKU cae en la partición que su sha256 dice.
     *
     * Es la comprobación que ninguna unitaria puede hacer, por el mismo
     * motivo que el resto de este archivo: la población que entra al digest
     * la decide el `FROM` de una tabla staged, y eso sólo existe cuando el
     * SQL se ensambla de verdad. Un digest de contenido calculado sobre una
     * población estrechada reportaría deriva permanente contra un espejo que
     * sí tiene los productos que la tienda muestra — la misma trampa que C2,
     * en el campo nuevo.
     */
    public function testTheContentPartitionsCoverExactlyTheActivePopulation(): void
    {
        $skus = $this->magentoActiveSkus();
        $checksums = self::$objectManager->get(ChecksumReaderInterface::class)
            ->getChecksums($this->aStoreId())[0];

        $this->assertSame(
            ContentDigest::PARTITION_COUNT,
            $checksums['partition_count'],
            'el esquema declarado no es el que el módulo calcula'
        );

        $conteoPorParticion = [];
        foreach ($checksums['content_partitions'] as $row) {
            $conteoPorParticion[$row['partition']] = $row['product_count'];
            $this->assertMatchesRegularExpression(
                '/^[0-9a-f]{64}$/',
                $row['content_digest'],
                'el digest de una partición no es un sha256 hex'
            );
        }

        // Recalculado desde los SKUs que Magento considera activos: si el
        // digest se computara sobre otra población (una estrechada por un
        // filtro propio, o una sin paginar), los conteos por partición no
        // coincidirían.
        $esperado = [];
        $digest = new ContentDigest();
        foreach ($skus as $sku) {
            $particion = $digest->partitionOf($sku);
            $esperado[$particion] = ($esperado[$particion] ?? 0) + 1;
        }
        ksort($esperado, SORT_STRING);
        ksort($conteoPorParticion, SORT_STRING);

        $this->assertSame($esperado, $conteoPorParticion);
        $this->assertSame(
            count($skus),
            array_sum($conteoPorParticion),
            'las particiones no suman la población activa'
        );
        $this->assertNotSame([], $conteoPorParticion, 'la prueba no afirmaría nada sin particiones');
    }

    /**
     * A3: el camino de activación de `/deltas` tiene que ser SATISFACIBLE.
     * Con `sinceTimestamp = 0` la consulta pide "toda versión cuyo
     * `created_in` sea posterior a 0", que sobre el filtro de Magento es
     * exactamente la población activa. Cuando el módulo agregaba además su
     * propia ventana por reloj, la conjunción devolvía menos filas (o, con el
     * id de versión en su valor mínimo, ninguna) y el mecanismo quedaba
     * muerto sin que nada rojo lo dijera.
     */
    public function testTheDeltaActivationPathSeesTheWholeActivePopulation(): void
    {
        if (!self::$objectManager->get(VersioningSchema::class)->isVersioned()) {
            $this->markTestSkipped('instancia sin columnas de versión: no hay camino de activación');
        }

        $page = self::$objectManager->get(DeltaReaderInterface::class)
            ->getChanges(sinceId: 0, limit: 5000, sinceTimestamp: 0)[0];

        $sentinelSkus = [];
        foreach ($page['items'] as $item) {
            if ((int) $item['change_id'] === 0) {
                $sentinelSkus[] = (string) $item['sku'];
                $this->assertSame('save', $item['event']);
            }
        }

        $expected = $this->magentoActiveSkus();
        sort($expected, SORT_STRING);
        sort($sentinelSkus, SORT_STRING);

        $this->assertSame(
            $expected,
            $sentinelSkus,
            'el camino de activación de /deltas no alcanza toda la población activa: algún '
            . 'filtro propio lo estrecha (A3 era esto, como consecuencia de C2)'
        );
    }

    /**
     * La población activa según Magento: un `Select` normal del framework,
     * sin nada nuestro encima. En una instancia con Staging, el renderer del
     * framework le inyecta la ventana de la versión APLICADA; en una sin
     * Staging no inyecta nada y devuelve la tabla entera, que ahí es lo mismo.
     *
     * @return list<string>
     */
    private function magentoActiveSkus(): array
    {
        $resource = self::$objectManager->get(ResourceConnection::class);
        $connection = $resource->getConnection();
        $select = $connection->select()
            ->from(['e' => $resource->getTableName('catalog_product_entity')], ['sku' => 'e.sku']);

        $skus = array_map('strval', $connection->fetchCol($select));

        if (count($skus) > self::MAX_CATALOG) {
            $this->markTestSkipped(
                'catálogo de ' . count($skus) . ' filas activas: por encima de ' . self::MAX_CATALOG
                . ' esta prueba sería un barrido, no una prueba'
            );
        }

        return $skus;
    }

    private function aStoreId(): int
    {
        $resource = self::$objectManager->get(ResourceConnection::class);
        $connection = $resource->getConnection();

        $storeId = $connection->fetchOne(
            $connection->select()
                ->from($resource->getTableName('store'), ['store_id'])
                ->where('store_id > 0')
                ->order('store_id ASC')
                ->limit(1)
        );

        $this->assertNotFalse($storeId, 'la instancia no tiene ninguna store view');

        return (int) $storeId;
    }
}
