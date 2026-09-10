<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface ChecksumReaderInterface
{
    /**
     * Conteo, huella (digest) del conjunto de SKUs activos del catálogo y
     * digest de CONTENIDO por partición, para que el lado Python
     * (`reconcile()`, `src/skudo/ingest/reconcile.py`) pueda comparar su
     * espejo contra Magento sin volver a paginar el catálogo entero.
     *
     * Tres respuestas, tres preguntas distintas:
     *
     * - `product_count`: ¿el espejo tiene la misma cantidad de productos?
     * - `sku_digest`: ¿tiene los MISMOS productos? (detecta un SKU borrado y
     *   otro creado en el mismo intervalo, que el conteo no ve)
     * - `content_partitions` + `partition_count` (H1): ¿tiene los mismos
     *   VALORES? Un `updated_at` cambiado en un SKU existente es invisible
     *   para las dos primeras, para siempre. Es una lista de
     *   `{partition, product_count, content_digest}` —sólo las particiones no
     *   vacías, ordenadas por partición— donde `partition` son los dos
     *   primeros caracteres hex de `sha256(sku)`. El lado Python recalcula el
     *   MISMO digest sobre su espejo y reporta QUÉ particiones divergen, para
     *   que el remedio sea dirigido y no "re-sincronizá el catálogo entero".
     *   Ver `Model\ContentDigest` para el esquema y para el límite declarado
     *   de lo que `updated_at` alcanza a ver.
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
     * @return mixed[] [{"product_count": int, "sku_digest": string,
     *                   "partition_count": int,
     *                   "content_partitions": [{"partition": string,
     *                                           "product_count": int,
     *                                           "content_digest": string}]}]
     */
    public function getChecksums(int $storeId): array;
}
