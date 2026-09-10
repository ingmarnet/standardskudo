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

from skudo.magento.client import MagentoApiError, MagentoClient


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


# --- C2: activaciones de versión programada ---------------------------------
#
# Con Magento_Staging, una actualización programada se vuelve activa cuando
# pasa su `created_in`, y en ese instante NO ocurre ningún evento: el tiempo
# simplemente transcurre, y un observer no puede suscribirse a eso. Por eso
# `/deltas` acepta `sinceTimestamp` y devuelve esos SKUs con `change_id: 0` y
# `last_change_id: null` — filas que no son de la cola y no deben mover el
# watermark de change_id.
#
# `iter_deltas` abortaba con RuntimeError ante exactamente esa forma: los dos
# lados tenían tests verdes afirmando mitades contradictorias del contrato.


def test_iter_deltas_sends_the_timestamp_only_on_the_first_page():
    """Las activaciones no se paginan por change_id: el módulo las adjunta a
    CADA página que recibe `sinceTimestamp`, así que pedirlas en todas sería
    reaplicar el mismo conjunto una vez por página. Pertenecen a una sola.

    Discrimina por la lista de valores, no por presencia: si se enviara en
    todas, la lista sería ['1000', '1000'] y no ['1000', None]."""
    sent: list[str | None] = []
    pages = {
        0: {"items": [{"change_id": 1, "sku": "A"}], "last_change_id": 1},
        1: {"items": [], "last_change_id": None},
    }

    def handler(request):
        sent.append(request.url.params.get("sinceTimestamp"))
        return skudo_response(pages[int(request.url.params["sinceId"])])

    list(make_client(handler).iter_deltas(0, since_timestamp=1000))

    assert sent == ["1000", None]


def test_iter_deltas_sends_no_timestamp_when_the_last_read_is_unknown():
    """Primera lectura de deltas de un tenant: no hay 'última lectura'. Mandar
    0 haría que `created_in > 0` capturara la versión activa de TODO el
    catálogo como si se acabara de activar."""
    sent: list[str | None] = []

    def handler(request):
        sent.append(request.url.params.get("sinceTimestamp"))
        return skudo_response({"items": [], "last_change_id": None})

    list(make_client(handler).iter_deltas(0))

    assert sent == [None]


def test_a_page_of_only_version_activations_is_delivered_and_ends_the_walk():
    """La forma que `DeltaReader::getChanges()` emite cuando la cola está vacía
    pero hay versiones recién activadas: items con `change_id: 0` y
    `last_change_id: null`. Antes esto era un RuntimeError; ahora se entrega
    (el consumidor necesita esos SKUs) y se termina, porque un `last_change_id`
    nulo significa que no queda nada de cola que paginar — seguir pidiendo
    devolvería la misma página para siempre."""
    def handler(request):
        return skudo_response(
            {"items": [{"change_id": 0, "sku": "PROGRAMADO", "event": "save"}],
             "last_change_id": None}
        )

    pages = list(make_client(handler).iter_deltas(0, since_timestamp=1000))

    assert len(pages) == 1
    assert pages[0]["items"][0]["sku"] == "PROGRAMADO"


def test_a_null_cursor_with_a_real_queue_row_still_aborts():
    """La guarda original NO se relaja del todo: un item con `change_id`
    distinto de cero es una fila real de cola, y si viene sin `last_change_id`
    no hay con qué avanzar el watermark — reaplicaríamos esa página para
    siempre. Solo el centinela `change_id: 0` está exento.

    Es lo que distingue este arreglo de 'borrar la guarda': con la guarda
    borrada, este caso pasaría en silencio."""
    def handler(request):
        return skudo_response(
            {"items": [{"change_id": 0, "sku": "PROGRAMADO"},
                       {"change_id": 88, "sku": "REAL"}],
             "last_change_id": None}
        )

    with pytest.raises(RuntimeError, match="last_change_id"):
        list(make_client(handler).iter_deltas(0, since_timestamp=1000))


# --- B1/M2: los errores del módulo llegan legibles y clasificables ---------
#
# Magento no manda el mensaje armado: manda la plantilla de `__()` y los
# argumentos por separado. Sobre HTTP real el tope de 100 SKUs devolvía
# `{"message": "no se pueden pedir más de %1 SKUs por llamada (se recibieron
# %2)", "parameters": [100, 101]}` y `raise_for_status()` de httpx lo tiraba
# entero: su mensaje es "Client error '400 Bad Request' for url ...", así que
# el motivo real no aparecía en ningún log.


def test_a_positional_error_template_is_interpolated():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "message": "no se pueden pedir más de %1 SKUs por llamada (se recibieron %2)",
                "parameters": [100, 101],
            },
        )

    with pytest.raises(MagentoApiError) as excinfo:
        make_client(handler).products_by_sku(1, ["A"])

    assert "no se pueden pedir más de 100 SKUs por llamada (se recibieron 101)" in str(excinfo.value)
    assert "%1" not in str(excinfo.value)
    assert excinfo.value.status_code == 400


def test_a_named_error_template_is_interpolated():
    """La otra forma observada: `%fieldName` con un mapa de parámetros."""

    def handler(request):
        return httpx.Response(
            400,
            json={
                "message": '"%fieldName" es obligatorio. Ingresá el valor y probá de nuevo.',
                "parameters": {"fieldName": "storeId"},
            },
        )

    with pytest.raises(MagentoApiError) as excinfo:
        make_client(handler).checksums(1)

    assert '"storeId" es obligatorio' in str(excinfo.value)
    assert "%fieldName" not in str(excinfo.value)


def test_the_longest_named_placeholder_wins():
    """Con `%field` y `%fieldName`, sustituir la corta primero partiría la
    larga y dejaría un `Name` colgando en el mensaje."""

    def handler(request):
        return httpx.Response(
            400,
            json={
                "message": "%field / %fieldName",
                "parameters": {"field": "A", "fieldName": "B"},
            },
        )

    with pytest.raises(MagentoApiError) as excinfo:
        make_client(handler).checksums(1)

    assert excinfo.value.message == "A / B"


def test_a_placeholder_without_a_value_is_left_visible():
    """No se borra: un `%2` visible dice "falta un dato acá", que es
    información; el hueco borrado miente sobre lo que el servidor dijo."""

    def handler(request):
        return httpx.Response(400, json={"message": "faltan %1 y %2", "parameters": [7]})

    with pytest.raises(MagentoApiError) as excinfo:
        make_client(handler).checksums(1)

    assert excinfo.value.message == "faltan 7 y %2"


def test_the_status_code_travels_so_a_caller_can_tell_input_from_server_fault():
    """M2 del lado del cliente: un 400 es entrada inválida (no reintentar) y
    un 5xx es fallo del servidor (reintentable). Sin `status_code` expuesto,
    quien reintente no puede distinguirlos y reintentaría para siempre un
    cursor ilegible."""

    def handler(request):
        return httpx.Response(400, json={"message": "cursor inválido", "parameters": []})

    with pytest.raises(MagentoApiError) as excinfo:
        list(make_client(handler).iter_products(1))

    assert excinfo.value.status_code == 400
    assert excinfo.value.path.endswith("/products")


def test_an_error_without_a_json_body_still_reports_what_arrived():
    """Un 401 sin cuerpo, o un 502 de un proxy: se reporta el texto crudo
    acotado, nunca un motivo inventado."""

    def handler(request):
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    with pytest.raises(MagentoApiError) as excinfo:
        make_client(handler).checksums(1)

    assert excinfo.value.status_code == 502
    assert "Bad Gateway" in excinfo.value.message


def test_a_successful_response_is_not_touched():
    """Contra-guarda: si `raise_for_status` lanzara para todo, cada test de
    arriba pasaría y el cliente no serviría para nada."""

    def handler(request):
        return skudo_response({"product_count": 7, "sku_digest": "d"})

    assert make_client(handler).checksums(1)["product_count"] == 7
