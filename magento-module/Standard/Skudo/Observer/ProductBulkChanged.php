<?php
declare(strict_types=1);

namespace Standard\Skudo\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Psr\Log\LoggerInterface;
use Standard\Skudo\Model\ChangeLog;
use Standard\Skudo\Model\SkuResolver;

/**
 * Alimenta la cola de cambios desde los eventos de escritura MASIVA de
 * producto. Es el arreglo del hallazgo H1.
 *
 * Lo que faltaba: `Observer\ProductChanged` escucha
 * `catalog_product_save_after`, y `Magento\Catalog\Model\Product\Action`
 * —la acción masiva "Actualizar atributos" del grid de admin, y la API que
 * usan las herramientas de carga por lotes de terceros— NO lo despacha.
 * `updateAttributes()` despacha únicamente
 * `catalog_product_attribute_update_before`
 * (`vendor/magento/module-catalog/Model/Product/Action.php:85`) y
 * `updateWebsites()` únicamente `catalog_product_to_website_change`
 * (:168). Medido en la instancia de desarrollo: las dos escriben, y ninguna
 * dejaba una sola fila en la cola.
 *
 * Y por qué la reconciliación no lo tapaba: `reconcile()` comparaba el
 * CONJUNTO de SKUs y su huella. Un valor cambiado en un SKU existente le era
 * invisible, para siempre. Esa mitad se arregla en `ChecksumReader` con el
 * digest de contenido por partición; esta clase es la otra mitad, la que
 * hace que el cambio se vea en segundos y no en la próxima reconciliación.
 *
 * POR QUÉ HACE FALTA IGUAL, aunque exista el digest de contenido: la
 * medición de H1 sobre la instancia de desarrollo muestra que
 * `catalog_product_entity.updated_at` NO se mueve en todos los caminos.
 * `updateAttributes()` sí lo escribe explícitamente
 * (`ResourceModel\Product\Action::updateAttributes()` mete
 * `ProductInterface::UPDATED_AT` en el lote), pero `updateWebsites()` toca
 * sólo `catalog_product_website` y deja `updated_at` intacto: ese cambio no
 * lo ve ningún digest de `(sku, updated_at)`, y sin este observer no lo
 * vería nada. Un producto que sale del website de Brasil seguiría
 * apareciendo en el espejo de Brasil indefinidamente.
 *
 * `_before` y no `_after`, porque es el único que Magento despacha en ese
 * camino. La consecuencia es que se registra la intención y no el hecho: si
 * la escritura falla o revierte después, queda una fila de cola que provoca
 * un refresco de un producto que no cambió. Es inofensivo —todo el camino de
 * refresco del ingestor es upsert por (tenant, sku, store view)— y es la
 * asimetría correcta: un refresco de más cuesta una petición, un cambio
 * perdido cuesta un valor rancio permanente.
 *
 * Nunca se queda callado. Un observer que registra cero filas y no dice nada
 * es peor que no tener observer: da la impresión de cobertura sin darla. Si
 * el evento llega sin la clave de ids que se espera, o si un id no resuelve
 * a ningún SKU, se deja rastro en el log del cliente.
 *
 * Cross-edition: no referencia ninguna clase de `Magento\Staging\*` ni
 * ramifica por edición. Los dos eventos son de `Magento_Catalog` y existen en
 * Community; la traducción de id a SKU vive en `Model\SkuResolver`, que hace
 * la pregunta de esquema con `tableColumnExists()`.
 */
class ProductBulkChanged implements ObserverInterface
{
    /**
     * Las claves con las que cada evento nombra su lista de ids. Se leen las
     * DOS en cada invocación en vez de ramificar por nombre de evento: el
     * observer se declara para los dos eventos en `etc/events.xml` y lo que
     * decide qué hacer es la carga que llegó, no cómo se llama. Si Magento
     * renombrara la clave en una versión futura, no queda un observer que
     * registra cero en silencio: queda una línea de log que lo nombra.
     *
     * - `product_ids`: `catalog_product_attribute_update_before`
     *   (`Product\Action::updateAttributes()`)
     * - `products`: `catalog_product_to_website_change`
     *   (`Product\Action::updateWebsites()`)
     */
    private const ID_KEYS = ['product_ids', 'products'];

    public function __construct(
        private readonly ChangeLog $log,
        private readonly SkuResolver $skuResolver,
        private readonly LoggerInterface $logger,
    ) {
    }

    public function execute(Observer $observer): void
    {
        $event = $observer->getEvent();
        $eventName = (string) $event->getName();

        $ids = [];
        $keyFound = false;
        foreach (self::ID_KEYS as $key) {
            $value = $event->getData($key);
            if ($value === null) {
                continue;
            }
            $keyFound = true;
            foreach ((array) $value as $id) {
                $ids[] = (int) $id;
            }
        }

        if (!$keyFound) {
            $this->logger->warning(sprintf(
                'Standard_Skudo: el evento %s llegó sin ninguna de las claves de ids '
                . 'que este observer conoce (%s), así que NO se registró ningún cambio '
                . 'en standard_skudo_change_log. El espejo del tenant puede quedar con '
                . 'valores rancios hasta la próxima reconciliación de contenido.',
                $eventName,
                implode(', ', self::ID_KEYS)
            ));
            return;
        }

        // Una selección vacía no es una pérdida: no hubo escritura que observar.
        if ($ids === []) {
            return;
        }

        $resolution = $this->skuResolver->resolve($ids);

        if ($resolution->unresolvedIds !== []) {
            $this->logger->warning(sprintf(
                'Standard_Skudo: %d de %d ids del evento %s no resolvieron a ningún SKU '
                . 'en catalog_product_entity y su cambio NO quedó registrado en '
                . 'standard_skudo_change_log (ids: %s). El espejo del tenant sostendrá '
                . 'el valor viejo de esos productos hasta la próxima reconciliación de '
                . 'contenido.',
                count($resolution->unresolvedIds),
                count(array_unique($ids)),
                $eventName,
                implode(',', array_slice($resolution->unresolvedIds, 0, 20))
            ));
        }

        // 'save' y no un evento propio: para el consumidor esto es "releé este
        // producto", que es lo mismo que un guardado. El vocabulario de la
        // columna `event` es save|delete y `delta_sync` decide por él.
        $this->log->recordMany($resolution->skus, 'save');
    }
}
