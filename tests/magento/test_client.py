"""Guardas de avance de los dos iteradores paginados.

Un módulo que no avanza el cursor no es una hipótesis: es un bug de PHP a un
`ORDER BY` de distancia. Sin guarda, `iter_deltas` entra en bucle infinito
pidiendo la misma página para siempre, y con `last_change_id: null` e items no
vacíos lanza un `TypeError` que no dice nada de lo que pasó. Un bucle infinito
en un ingestor no se nota: se nota semanas después, como espejo desactualizado.
"""

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
