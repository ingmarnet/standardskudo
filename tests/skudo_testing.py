"""Utilidades compartidas por los tests. En `tests/` y no en `src/` porque no
son parte del producto: describen la forma del CABLE que el módulo Magento
emite, para que ningún mock vuelva a describir otra."""

from datetime import UTC, datetime

import httpx


def skudo_response(payload: dict, status_code: int = 200) -> httpx.Response:
    """Respuesta HTTP con la forma que el módulo emite DE VERDAD.

    Standard_Skudo envuelve todo payload un nivel (`WebApiEnvelope::wrap()`,
    `return [$payload]`) porque `ServiceOutputProcessor::convertValue()`
    reindexa el primer nivel de todo `@return mixed[]` y, sin envoltorio,
    descarta las claves del payload sobre el cable — el hallazgo B1, que
    sobrevivió doce revisiones porque TODOS los mocks de este lado devolvían
    la forma sin envolver.

    Que exista este helper es lo que impide volver a escribir un mock con la
    forma equivocada por descuido; quien afirma el envoltorio contra el
    ServiceOutputProcessor real es
    `magento-module/.../Test/Unit/WebApi/ServiceOutputEnvelopeTest.php`, y
    quien afirma que el cliente lo exige es
    `tests/magento/test_client.py::test_a_bare_payload_is_rejected_...`.
    """
    return httpx.Response(status_code, json=[payload])


# Instante con el que las fixtures de espejo de esta suite escriben
# `magento_updated_at`. Existe para que el doble de `/checksums` pueda
# construir el digest de contenido que el espejo va a recalcular.
MIRRORED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def checksums_payload(
    skus: list[str], updated_at: datetime | None = MIRRORED_AT
) -> dict:
    """Payload de `/checksums` con la forma COMPLETA que el módulo emite.

    Desde H1 la respuesta no son dos campos sino cuatro: al conteo y a la
    huella del conjunto de SKUs se suman `partition_count` y
    `content_partitions` (ver `Model\\ContentDigest` del módulo y
    `Api\\ChecksumReaderInterface`). `reconcile()` ABORTA si faltan, a
    propósito: un módulo sin digest de contenido no puede detectar un valor
    rancio, y reportar "sin deriva" en ese caso es exactamente el defecto que
    H1 cierra.

    Por eso este helper existe y los dobles no arman el diccionario a mano:
    es la misma razón que `skudo_response`. Un mock con la forma vieja no
    falla describiendo mal el cable —falla haciendo pasar por verde un
    escenario que en producción abortaría.
    """
    from skudo.ingest.reconcile import PARTITION_COUNT, content_partitions, sku_digest

    rows = [(sku, updated_at) for sku in skus]
    return {
        "product_count": len(skus),
        "sku_digest": sku_digest(skus),
        "partition_count": PARTITION_COUNT,
        "content_partitions": [
            {"partition": partition, "product_count": count, "content_digest": digest}
            for partition, (count, digest) in sorted(content_partitions(rows).items())
        ],
    }
