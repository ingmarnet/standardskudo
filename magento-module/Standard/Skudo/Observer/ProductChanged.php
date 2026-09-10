<?php
declare(strict_types=1);

namespace Standard\Skudo\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Standard\Skudo\Model\ChangeLog;

/**
 * Alimenta la cola de cambios desde los eventos de guardado/borrado de
 * producto POR EL MODELO: el admin de un producto, la API REST de producto,
 * cualquier `save()`/`delete()`.
 *
 * Lo que este observer NO cubre, y quién lo cubre:
 *
 * - Las escrituras MASIVAS (`Product\Action::updateAttributes()` y
 *   `::updateWebsites()`, que son la acción del grid de admin y la API de las
 *   herramientas de carga por lotes) no despachan
 *   `catalog_product_save_after` en absoluto. Las cubre
 *   `Observer\ProductBulkChanged`, suscrito a los dos eventos que esos
 *   caminos SÍ despachan. Eso era el hallazgo H1: durante todo S0 este
 *   observer fue la única fuente de la cola y no veía ninguna de las dos.
 * - La activación de una versión programada (Magento_Staging) no despacha
 *   ningún evento porque no ocurre nada: pasa el tiempo. Se resuelve en
 *   `DeltaReader::getChanges()`; ver el comentario de ese método.
 * - Una importación de `CatalogImportExport` y un `UPDATE` de SQL directo
 *   escriben por debajo del modelo y no despachan nada. Los cubre el digest
 *   de contenido por partición de `/checksums` (ver `Model\ContentDigest`),
 *   en la medida en que muevan `catalog_product_entity.updated_at` — que la
 *   importación sí lo mueve y un `UPDATE` a una tabla satélite no.
 */
class ProductChanged implements ObserverInterface
{
    public function __construct(private readonly ChangeLog $log)
    {
    }

    public function execute(Observer $observer): void
    {
        $product = $observer->getEvent()->getData('product');
        $sku = $product?->getSku();

        if ($sku === null || $sku === '') {
            return;
        }

        $event = str_contains($observer->getEvent()->getName(), 'delete') ? 'delete' : 'save';
        $this->log->record((string) $sku, $event);
    }
}
