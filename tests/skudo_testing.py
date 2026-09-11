"""Utilidades compartidas por los tests. En `tests/` y no en `src/` porque no
son parte del producto: describen la forma del CABLE que el módulo Magento
emite, para que ningún mock vuelva a describir otra."""

from datetime import UTC, datetime

import httpx

from skudo.mirror.attributes import upsert_options
from skudo.mirror.categories import set_products_categories
from skudo.mirror.products import record_values, upsert_records


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


def upsert_record(
    session,
    tenant_id: int,
    store_view_magento_id: int,
    identity,
    effective: dict,
    provenance: dict,
    magento_updated_at,
    *,
    attribute_set_id: int | None = None,
    type_id: str | None = None,
    website_ids: list[int] | None = None,
    sync_generation: int | None = None,
) -> None:
    """Escribe UNA fila del espejo. Conveniencia de los tests, no del producto.

    Vivía en `skudo.mirror.products` hasta H3, cuando la escritura pasó a ser
    por LOTE (`upsert_records`, una sentencia multi-fila por página: el coste
    dominante era compilar el SQL una vez por producto). El envoltorio de una
    fila se quedó sin ningún llamador en `src/`, y
    `tests/mirror/test_write_paths_are_reachable.py` lo señaló — que es
    exactamente su trabajo: una función de escritura que solo usan los tests
    afirma un camino de ingesta que no existe.

    Así que el envoltorio se mudó acá, donde sí tiene llamadores y donde su
    naturaleza queda clara: es andamiaje para sembrar filas en un test. Llama
    a las MISMAS funciones que el producto (`record_values` +
    `upsert_records`), así que un test que siembra con esto sigue ejerciendo
    el camino de escritura real y no una copia.
    """
    upsert_records(
        session,
        [
            record_values(
                tenant_id,
                store_view_magento_id,
                identity,
                effective,
                provenance,
                magento_updated_at,
                attribute_set_id=attribute_set_id,
                type_id=type_id,
                website_ids=website_ids,
                sync_generation=sync_generation,
            )
        ],
    )


def set_product_categories(
    session, tenant_id: int, sku: str, category_magento_ids: list[int]
) -> None:
    """Reemplaza el conjunto de categorías de UN producto. Misma historia que
    `upsert_record`: el producto escribe por lote (`set_products_categories`) y
    esta forma de a uno es conveniencia de los tests."""
    set_products_categories(session, tenant_id, {sku: category_magento_ids})


def upsert_option(
    session,
    tenant_id: int,
    attribute_code: str,
    option_id: int,
    labels: dict[int, str],
    *,
    sync_generation: int,
) -> None:
    """Escribe UNA opción con sus etiquetas. Conveniencia de los tests.

    Misma historia que `upsert_record`: la escritura de opciones pasó a ser por
    LOTE (`upsert_options`, tres sentencias por página en vez de cuatro por
    opción) y el envoltorio de a una se quedó sin llamadores en `src/`.
    `tests/mirror/test_write_paths_are_reachable.py` lo habría señalado, que es
    exactamente su trabajo: una función de escritura que sólo usan los tests
    afirma un camino de ingesta que no existe.

    Delega en la MISMA función que el producto, así que un test que siembra con
    esto sigue ejerciendo el camino de escritura real y no una copia suya.
    """
    upsert_options(
        session,
        tenant_id,
        [{"attribute_code": attribute_code, "option_id": option_id, "labels": labels}],
        sync_generation=sync_generation,
    )
