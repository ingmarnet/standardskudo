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
        return httpx.Response(200, json=pages[since])

    assert [p["last_change_id"] for p in make_client(handler).iter_deltas(0)] == [1, 2]


def test_iter_deltas_aborts_when_the_cursor_does_not_advance():
    def handler(request):
        return httpx.Response(
            200, json={"items": [{"change_id": 7, "sku": "A"}], "last_change_id": 7}
        )

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_deltas(7))


def test_iter_deltas_aborts_when_last_change_id_is_null_with_items():
    def handler(request):
        return httpx.Response(
            200, json={"items": [{"change_id": 7, "sku": "A"}], "last_change_id": None}
        )

    with pytest.raises(RuntimeError, match="last_change_id"):
        list(make_client(handler).iter_deltas(0))


def test_iter_deltas_never_yields_a_page_it_cannot_advance_past():
    """La guarda va ANTES del yield: `delta_sync` escribe el watermark con el
    `last_change_id` de la página que consumió, así que una página con un
    cursor inutilizable no debe llegarle nunca."""
    def handler(request):
        return httpx.Response(
            200, json={"items": [{"change_id": 7, "sku": "A"}], "last_change_id": None}
        )

    consumed = 0
    with pytest.raises(RuntimeError):
        for _page in make_client(handler).iter_deltas(0):
            consumed += 1
    assert consumed == 0


def test_iter_products_walks_pages_while_the_cursor_advances():
    def handler(request):
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(200, json={"items": [{"sku": "A"}], "next_cursor": "c2"})
        return httpx.Response(200, json={"items": [{"sku": "B"}], "next_cursor": None})

    assert len(list(make_client(handler).iter_products(1))) == 2


def test_iter_products_aborts_when_the_cursor_repeats():
    def handler(request):
        return httpx.Response(200, json={"items": [{"sku": "A"}], "next_cursor": "mismo"})

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_products(1))


def test_iter_attributes_walks_pages_while_the_cursor_advances():
    def handler(request):
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(
                200, json={"items": [{"code": "color"}], "next_cursor": "c2"}
            )
        return httpx.Response(200, json={"items": [{"code": "size"}], "next_cursor": None})

    assert len(list(make_client(handler).iter_attributes())) == 2


def test_iter_attributes_aborts_when_the_cursor_repeats():
    def handler(request):
        return httpx.Response(200, json={"items": [{"code": "color"}], "next_cursor": "mismo"})

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_attributes())


def test_iter_attributes_stops_when_next_cursor_is_null():
    """Sin esta guarda, un `next_cursor: null` real (fin de la lista) sigue
    pidiendo la misma página vacía porque no distingue 'terminó' de 'no avanzó'."""

    def handler(request):
        return httpx.Response(200, json={"items": [{"code": "color"}], "next_cursor": None})

    pages = list(make_client(handler).iter_attributes())

    assert len(pages) == 1


def test_iter_categories_walks_pages_while_the_cursor_advances():
    def handler(request):
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(
                200, json={"items": [{"category_id": 1}], "next_cursor": "c2"}
            )
        return httpx.Response(200, json={"items": [{"category_id": 2}], "next_cursor": None})

    assert len(list(make_client(handler).iter_categories())) == 2


def test_iter_categories_aborts_when_the_cursor_repeats():
    def handler(request):
        return httpx.Response(
            200, json={"items": [{"category_id": 1}], "next_cursor": "mismo"}
        )

    with pytest.raises(RuntimeError, match="no avanza"):
        list(make_client(handler).iter_categories())


def test_iter_categories_stops_when_next_cursor_is_null():
    """Sin esta guarda, un `next_cursor: null` real (fin del catálogo de
    categorías) seguiría pidiendo la misma página vacía en vez de terminar."""

    def handler(request):
        return httpx.Response(200, json={"items": [{"category_id": 1}], "next_cursor": None})

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
        return httpx.Response(200, json={"items": response_items or []})

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
