<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\Exception\InputException;
use Magento\Framework\Exception\NoSuchEntityException;
use Magento\Store\Model\StoreManagerInterface;

/**
 * Único lugar que decide si un `storeId` recibido por la red existe.
 *
 * M3 — el defecto que esta clase cierra, verificado sobre HTTP real:
 * `/products?storeId=999` devolvía **200** con los items del catálogo y
 * `store_values: {}` en todos, y `/signals?storeId=999` devolvía **200** con
 * `{"items":[]}`. Ninguno de los dos comprobaba que la store view existiera.
 *
 * Por qué eso es grave y no cosmético: un `storeId` mal escrito en la
 * configuración de un tenant hace que el ingestor espeje los valores
 * GLOBALES como si fueran la verdad de esa tienda, sin un solo error. Es el
 * patrón de "desconocido presentado como hecho" en su forma más silenciosa —
 * la ausencia de filas de override es indistinguible de "esta tienda no
 * tiene overrides", que es una afirmación legítima para una tienda que sí
 * existe. Y como `/checksums` tampoco filtra por store, `reconcile()`
 * compararía dos lados de acuerdo sobre un espejo equivocado y diría "sin
 * deriva".
 *
 * La existencia se pregunta al `StoreManagerInterface`, no a la tabla
 * `store`: es la MISMA fuente de verdad que alimenta
 * `/environment.store_views`, así que un tenant que configura una store view
 * que la sonda de entorno reportó nunca puede recibir este error, y una que
 * la sonda no reportó lo recibe siempre.
 *
 * `InputException` y no `NoSuchEntityException`: el framework de web API
 * mapea la primera a 400 y la segunda a 404. El `storeId` es un PARÁMETRO de
 * la petición, no el recurso que la ruta nombra; 400 es lo que un cliente
 * necesita para no reintentar. Es el mismo criterio que el tope de 100 SKUs
 * y que el cursor ilegible (ver `Model\Cursor`).
 */
class StoreViewGuard
{
    /** @var array<int, bool> */
    private array $exists = [];

    public function __construct(private readonly StoreManagerInterface $storeManager)
    {
    }

    /**
     * @throws InputException cuando la instancia no conoce ese store view.
     */
    public function assertExists(int $storeId): void
    {
        // Memoizado por la misma razón que EntityKeyResolver: `full_sync`
        // pagina cientos de miles de productos pidiendo siempre el mismo
        // storeId, y `getStore()` toca el store manager en cada llamada.
        if (!isset($this->exists[$storeId])) {
            try {
                $this->storeManager->getStore($storeId);
                $this->exists[$storeId] = true;
            } catch (NoSuchEntityException) {
                $this->exists[$storeId] = false;
            }
        }

        if (!$this->exists[$storeId]) {
            throw new InputException(__(
                'no existe la store view %1 en esta instancia; los ids válidos son los que '
                . 'reporta /environment en store_views',
                $storeId
            ));
        }
    }
}
