<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface ProductReaderInterface
{
    /**
     * Lee una página de productos con sus valores global y de store view.
     *
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que el `foreach` devuelva la misma lista de un
     * elemento y el payload llegue intacto. El lado Python desenvuelve con
     * `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @param int $storeId
     * @param int $limit
     * @param string|null $cursor
     * @return mixed[] [{"items": [...], "next_cursor": string|null}]
     */
    public function getPage(int $storeId, int $limit = 500, ?string $cursor = null): array;

    /**
     * Relee un conjunto concreto de SKUs. Complemento del endpoint de deltas:
     * la cola dice QUÉ cambió, esto trae el estado nuevo.
     *
     * POST (no GET): la identidad viaja como texto sin normalizar, así que un
     * SKU puede contener una coma, y `delta_sync` puede pedir hasta 1000 SKUs
     * de una vez, cuyo tamaño como query string un proxy típico rechaza.
     *
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que el `foreach` devuelva la misma lista de un
     * elemento y el payload llegue intacto. El lado Python desenvuelve con
     * `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @param int $storeId
     * @param string[] $skus
     * @return mixed[] [{"items": [...]}]
     */
    public function getBySku(int $storeId, array $skus): array;
}
