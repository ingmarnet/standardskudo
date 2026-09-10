"""Guardas de avance de los dos iteradores paginados.

Un módulo que no avanza el cursor no es una hipótesis: es un bug de PHP a un
`ORDER BY` de distancia. Sin guarda, `iter_deltas` entra en bucle infinito
pidiendo la misma página para siempre, y con `last_change_id: null` e items no
vacíos lanza un `TypeError` que no dice nada de lo que pasó. Un bucle infinito
en un ingestor no se nota: se nota semanas después, como espejo desactualizado.
"""

import json

import httpx
import pytest
from skudo_testing import skudo_response

from skudo.magento.client import MagentoClient


def make_client(handler) -> MagentoClient:
    return MagentoClient("https://x.test", "t", transport=httpx.MockTransport(handler))


def test_iter_deltas_walks_pages_while_the_cursor_advances():
    pages = {
        0: {"items": [{"change_id": 1, "sku": "A"}], "last_change_id": 1},
        1: {"items": [{"change_id": 2, "sku": "B"}], "last_change_id": 2},
        2: {"items": [], "last_change_id": None},
    }

    def handler(request):
        since = int(request.url.params["sinceId"])
        return skudo_response(pages[since])

    assert [p["last_change_id"] for p in make_client(handler).iter_deltas(0)] == [1, 2]


@pytest.mark.timeout(5)
def test_iter_deltas_aborts_when_the_cursor_does_not_advance():
    """Marcado con timeout a propósito: si la guarda se revirtiera, `list(...)`
    sobre este iterador entraría en bucle infinito en vez de fallar, y sin
    límite el test colgaría la suite entera en lugar de reportar nada."""
    def handler(request):
        return skudo_response({"items": [{"change_id": 7, "sku": "A"}], "last_change_id": 7})

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_deltas(7))


def test_iter_deltas_aborts_when_last_change_id_is_null_with_items():
    def handler(request):
        return skudo_response({"items": [{"change_id": 7, "sku": "A"}], "last_change_id": None})

    with pytest.raises(RuntimeError, match="last_change_id"):
        list(make_client(handler).iter_deltas(0))


def test_iter_deltas_never_yields_a_page_it_cannot_advance_past():
    """La guarda va ANTES del yield: `delta_sync` escribe el watermark con el
    `last_change_id` de la página que consumió, así que una página con un
    cursor inutilizable no debe llegarle nunca."""
    def handler(request):
        return skudo_response({"items": [{"change_id": 7, "sku": "A"}], "last_change_id": None})

    consumed = 0
    with pytest.raises(RuntimeError):
        for _page in make_client(handler).iter_deltas(0):
            consumed += 1
    assert consumed == 0


def test_iter_products_walks_pages_while_the_cursor_advances():
    def handler(request):
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return skudo_response({"items": [{"sku": "A"}], "next_cursor": "c2"})
        return skudo_response({"items": [{"sku": "B"}], "next_cursor": None})

    assert len(list(make_client(handler).iter_products(1))) == 2


@pytest.mark.timeout(5)
def test_iter_products_aborts_when_the_cursor_repeats():
    """Timeout acotado: sin la guarda, este iterador nunca termina."""
    def handler(request):
        return skudo_response({"items": [{"sku": "A"}], "next_cursor": "mismo"})

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_products(1))


def test_iter_attributes_walks_pages_while_the_cursor_advances():
    def handler(request):
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return skudo_response({"items": [{"code": "color"}], "next_cursor": "c2"})
        return skudo_response({"items": [{"code": "size"}], "next_cursor": None})

    assert len(list(make_client(handler).iter_attributes())) == 2


@pytest.mark.timeout(5)
def test_iter_attributes_aborts_when_the_cursor_repeats():
    """Timeout acotado: sin la guarda, este iterador nunca termina."""
    def handler(request):
        return skudo_response({"items": [{"code": "color"}], "next_cursor": "mismo"})

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_attributes())


def test_iter_attributes_stops_when_next_cursor_is_null():
    """Sin esta guarda, un `next_cursor: null` real (fin de la lista) sigue
    pidiendo la misma página vacía porque no distingue 'terminó' de 'no avanzó'."""

    def handler(request):
        return skudo_response({"items": [{"code": "color"}], "next_cursor": None})

    pages = list(make_client(handler).iter_attributes())

    assert len(pages) == 1


def test_iter_categories_walks_pages_while_the_cursor_advances():
    def handler(request):
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return skudo_response({"items": [{"category_id": 1}], "next_cursor": "c2"})
        return skudo_response({"items": [{"category_id": 2}], "next_cursor": None})

    assert len(list(make_client(handler).iter_categories())) == 2


@pytest.mark.timeout(5)
def test_iter_categories_aborts_when_the_cursor_repeats():
    """Timeout acotado: sin la guarda, este iterador nunca termina."""
    def handler(request):
        return skudo_response({"items": [{"category_id": 1}], "next_cursor": "mismo"})

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_categories())


def test_iter_categories_stops_when_next_cursor_is_null():
    """Sin esta guarda, un `next_cursor: null` real (fin del catálogo de
    categorías) seguiría pidiendo la misma página vacía en vez de terminar."""

    def handler(request):
        return skudo_response({"items": [{"category_id": 1}], "next_cursor": None})

    pages = list(make_client(handler).iter_categories())

    assert len(pages) == 1


def _capturing_client(response_items=None):
    """Cliente que registra el cuerpo de cada petición a /products-by-sku."""
    captured: list[dict] = []

    def handler(request):
        captured.append(
            {
                "method": request.method,
                "body": json.loads(request.content),
                "url": str(request.url),
            }
        )
        return skudo_response({"items": response_items or []})

    return make_client(handler), captured


def test_a_sku_containing_a_comma_is_not_split_into_two_lookups():
    """`",".join(skus)` partía en dos un SKU con coma: el producto real nunca se
    refrescaba, y en silencio. Viola la regla de identidad como texto de esta
    misma rama, donde 'ABC,123' es un SKU y no dos."""
    client, captured = _capturing_client()

    client.products_by_sku(1, ["ABC,123", "PLANO"])

    assert captured[0]["body"]["skus"] == ["ABC,123", "PLANO"]


def test_the_lookup_travels_as_a_post_with_a_json_body():
    """`delta_sync` pasa hasta 1000 SKUs. En un GET son ~20 KB de query string,
    que nginx rechaza con 414 mucho antes de llegar a PHP."""
    client, captured = _capturing_client()

    client.products_by_sku(1, ["SKU1"])

    assert captured[0]["method"] == "POST"
    assert captured[0]["body"] == {"storeId": 1, "skus": ["SKU1"]}
    assert "skus=" not in captured[0]["url"]


def test_skus_are_chunked_to_at_most_one_hundred_per_request():
    client, captured = _capturing_client()
    skus = [f"SKU{i}" for i in range(250)]

    client.products_by_sku(1, skus)

    assert [len(c["body"]["skus"]) for c in captured] == [100, 100, 50]
    # Cada SKU se pide exactamente una vez: ni se pierde ni se duplica.
    requested = [sku for c in captured for sku in c["body"]["skus"]]
    assert requested == skus


def test_items_from_every_chunk_are_returned():
    client, captured = _capturing_client(response_items=[{"sku": "X"}])

    items = client.products_by_sku(1, [f"SKU{i}" for i in range(150)])

    assert len(captured) == 2
    assert len(items) == 2


def test_an_empty_sku_list_makes_no_request():
    client, captured = _capturing_client()

    assert client.products_by_sku(1, []) == []
    assert captured == []


# --- B1: la forma del CABLE, no la que devuelve el modelo --------------------
#
# Magento pasa todo retorno de web API por `ServiceOutputProcessor::process()`,
# que para un `@return mixed[]` reindexa el primer nivel y descarta sus claves.
# El módulo envuelve por eso cada payload un nivel (`WebApiEnvelope::wrap()`) y
# el cliente lo desenvuelve. Estas dos pruebas afirman los dos lados de esa
# frontera: que el envoltorio se exige, y que se desenvuelve de verdad.
#
# La prueba que afirma el envoltorio contra el ServiceOutputProcessor REAL vive
# del lado PHP (Test/Unit/WebApi/ServiceOutputEnvelopeTest.php); acá se afirma
# que el cliente no acepta la forma vieja en silencio.


def test_a_bare_payload_is_rejected_with_a_message_that_names_the_envelope():
    """La forma que un módulo SIN envoltorio pone en el cable, verificada por el
    revisor contra la instalación real: el objeto de primer nivel llega
    reindexado como lista. Antes de esta guarda eso reventaba tres funciones más
    abajo con un `AttributeError` sobre `list` que no decía nada; ahora falla
    donde se puede leer la causa.

    Discrimina porque `httpx.Response(200, json={...})` —el payload sin
    envolver, que es lo que TODOS los mocks de este repositorio devolvían antes
    de B1— tiene que hacer fallar esta llamada. Si `unwrap` volviera a ser
    `response.json()`, no se levantaría nada y el test fallaría.
    """
    def handler(request):
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    with pytest.raises(RuntimeError, match="envuelto"):
        list(make_client(handler).iter_products(1))


def test_the_reindexed_list_a_module_without_the_envelope_emits_is_rejected():
    """La forma EXACTA del transcript del revisor: `{"items": [...],
    "next_cursor": "skudo1:5"}` sale de ServiceOutputProcessor como
    `[[...], "skudo1:5"]`. Tiene dos elementos, así que la guarda la rechaza por
    longitud además de por tipo del primero."""
    def handler(request):
        return httpx.Response(200, json=[[{"sku": "A"}], "skudo1:5"])

    with pytest.raises(RuntimeError, match="envuelto"):
        list(make_client(handler).iter_products(1))


def test_the_wrapped_payload_is_unwrapped_into_the_object_the_caller_expects():
    """El otro lado de la frontera: envuelto, el cliente entrega el objeto —con
    sus claves— y no la lista de un elemento."""
    def handler(request):
        return skudo_response({"items": [{"sku": "A"}], "next_cursor": None})

    (page,) = list(make_client(handler).iter_products(1))

    assert page == {"items": [{"sku": "A"}], "next_cursor": None}


def test_checksums_returns_the_object_and_not_the_reindexed_pair():
    """`reconcile()` hace `remote["sku_digest"]`. Sin envoltorio, el cable trae
    `[0, "e3b0..."]` y eso es un `TypeError` sobre índices de lista."""
    def handler(request):
        return skudo_response({"product_count": 3, "sku_digest": "abc"})

    assert make_client(handler).checksums(1) == {"product_count": 3, "sku_digest": "abc"}
