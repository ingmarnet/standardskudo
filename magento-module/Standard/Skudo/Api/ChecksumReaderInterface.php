<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface ChecksumReaderInterface
{
    /**
     * Conteo y huella (digest) del conjunto de SKUs activos del catálogo,
     * para que el lado Python (`reconcile()`, `src/skudo/ingest/reconcile.py`)
     * pueda comparar su espejo contra Magento sin volver a paginar el
     * catálogo entero.
     *
     * Ruling 2 (S0 Task 13): $storeId se acepta por simetría de interfaz y
     * uso futuro, pero HOY no filtra nada — ver ChecksumReader para el
     * porqué (mirror_count/sku_digest del espejo se calculan sobre TODO el
     * catálogo por store view, no una vista filtrada por visibilidad).
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
     * @return mixed[] [{"product_count": int, "sku_digest": string}]
     */
    public function getChecksums(int $storeId): array;
}
