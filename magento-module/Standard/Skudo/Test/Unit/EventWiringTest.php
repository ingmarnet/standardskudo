<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit;

use Magento\Framework\Event\ObserverInterface;
use PHPUnit\Framework\TestCase;

/**
 * H1: el observer más correcto del mundo no registra nada si `etc/events.xml`
 * no lo suscribe, y ninguna prueba unitaria de la clase puede notarlo —el
 * defecto H1 fue exactamente eso durante todo S0: `ProductChanged` funcionaba
 * y estaba suscrito a dos eventos que los caminos masivos no despachan.
 *
 * Esta prueba afirma el archivo de cableado: qué eventos se escuchan, con qué
 * clase, y que esa clase existe y es un observer de verdad. Borrar una
 * entrada de `events.xml` la rompe nombrando el evento que falta.
 *
 * Los cuatro eventos, y por qué cada uno:
 *
 * - `catalog_product_save_after` / `catalog_product_delete_after`: el camino
 *   del modelo (admin de un producto, API REST de producto).
 * - `catalog_product_attribute_update_before`: lo ÚNICO que despacha
 *   `Product\Action::updateAttributes()`, la acción masiva del grid de admin
 *   y la API de las herramientas de carga por lotes
 *   (`vendor/magento/module-catalog/Model/Product/Action.php:85`).
 * - `catalog_product_to_website_change`: lo único que despacha
 *   `Product\Action::updateWebsites()` (:168), y el único camino medido donde
 *   `catalog_product_entity.updated_at` no se mueve.
 */
class EventWiringTest extends TestCase
{
    private const EXPECTED = [
        'catalog_product_save_after' => \Standard\Skudo\Observer\ProductChanged::class,
        'catalog_product_delete_after' => \Standard\Skudo\Observer\ProductChanged::class,
        'catalog_product_attribute_update_before' => \Standard\Skudo\Observer\ProductBulkChanged::class,
        'catalog_product_to_website_change' => \Standard\Skudo\Observer\ProductBulkChanged::class,
    ];

    public function testEveryWriteEventTheModuleDependsOnIsSubscribed(): void
    {
        $wired = $this->wiring();

        foreach (self::EXPECTED as $event => $observerClass) {
            $this->assertArrayHasKey(
                $event,
                $wired,
                "etc/events.xml no suscribe {$event}: las escrituras de ese camino no "
                . 'dejarían rastro en la cola de cambios'
            );
            $this->assertSame($observerClass, $wired[$event]);
        }
    }

    public function testEverySubscribedObserverExistsAndIsAnObserver(): void
    {
        $wired = $this->wiring();
        $this->assertNotSame([], $wired, 'la prueba no afirmaría nada sin cableado');

        foreach ($wired as $event => $class) {
            $this->assertTrue(class_exists($class), "{$event} apunta a una clase inexistente: {$class}");
            $this->assertContains(
                ObserverInterface::class,
                class_implements($class) ?: [],
                "{$class} está suscrito a {$event} y no implementa ObserverInterface"
            );
        }
    }

    /**
     * Contra-guarda: si `events.xml` declarara un evento de escritura de
     * producto que esta prueba no conoce, la lista EXPECTED se habría
     * desfasado del cableado sin que nada avisara. Se exige la igualdad de
     * conjuntos, no la inclusión.
     */
    public function testTheWiringDeclaresNothingThisTestDoesNotKnowAbout(): void
    {
        $this->assertSame(
            array_keys(self::EXPECTED),
            array_keys($this->wiring())
        );
    }

    /**
     * @return array<string, string> evento => clase del observer
     */
    private function wiring(): array
    {
        $path = dirname(__DIR__, 2) . '/etc/events.xml';
        $this->assertFileExists($path);

        $xml = simplexml_load_file($path);
        $this->assertNotFalse($xml, 'etc/events.xml no es XML válido');

        $wired = [];
        foreach ($xml->event as $event) {
            $wired[(string) $event['name']] = (string) $event->observer[0]['instance'];
        }

        return $wired;
    }
}
