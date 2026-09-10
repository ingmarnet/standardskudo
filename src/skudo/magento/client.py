from collections.abc import Iterator

import httpx

from skudo.magento.environment import EnvironmentProfile, parse_environment

# Cuántos SKUs se piden por petición a /products-by-sku. `delta_sync` puede
# traer 1000 cambios en una página; mandarlos juntos es un cuerpo grande y una
# consulta `IN (...)` de 1000 elementos en el módulo. 100 mantiene las dos
# cosas en tamaños que Magento y Postgres manejan sin sorpresas.
PRODUCTS_BY_SKU_CHUNK = 100


def unwrap(response: httpx.Response) -> dict:
    """Desenvuelve el payload de una respuesta del módulo Standard_Skudo.

    Magento no serializa lo que un método de web API devuelve: lo pasa antes
    por `ServiceOutputProcessor::convertValue()`, que para un `@return mixed[]`
    hace `foreach ($data as $datum) { $result[] = $datum; }` y REINDEXA el
    primer nivel, descartando sus claves. Un `{"items": [...], "next_cursor":
    "x"}` llegaba acá como `[[...], "x"]`: `page.get("next_cursor")` reventaba
    con `AttributeError` sobre una lista, y `parse_environment` con un error de
    pydantic — sobre HTTP real, en los OCHO endpoints a la vez, mientras cada
    test de los dos lados afirmaba en verde la forma anterior a esa conversión.

    El módulo envuelve ahora cada payload un nivel (`WebApiEnvelope::wrap()`,
    `return [$payload]`), que el `foreach` deja intacto, y acá se desenvuelve.

    La comprobación es explícita y no un `[0]` a secas para que un módulo viejo
    —o uno al que alguien le revierta el envoltorio— falle con un mensaje que
    nombra la causa, en vez de con un `AttributeError` tres funciones más
    abajo que no explica nada.
    """
    body = response.json()
    if not isinstance(body, list) or len(body) != 1 or not isinstance(body[0], dict):
        raise RuntimeError(
            f"{response.request.url.path} no devolvió el payload envuelto que "
            "Standard_Skudo declara: se esperaba una lista de un solo objeto "
            "(`[<payload>]`, ver Model\\WebApiEnvelope) y llegó "
            f"{type(body).__name__} {body!r:.200}. Un módulo sin el envoltorio "
            "pierde las claves de primer nivel en ServiceOutputProcessor."
        )
    return body[0]


class MagentoClient:
    """Cliente del módulo Standard_Skudo. Solo lectura en S0."""

    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/rest/V1/skudo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=transport,
        )

    def environment(self) -> EnvironmentProfile:
        response = self._client.get("/environment")
        response.raise_for_status()
        return parse_environment(unwrap(response))

    def iter_products(self, store_id: int, limit: int = 500) -> Iterator[dict]:
        """Recorre el catálogo página a página. Cada yield es una página completa.

        Con guarda de avance: un módulo que devuelve el mismo `next_cursor`
        mantiene el bucle pidiendo la misma página para siempre. Un bucle
        infinito en un ingestor no se nota mientras pasa; se nota semanas
        después, como espejo desactualizado sin causa aparente.
        """
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"storeId": store_id, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = self._client.get("/products", params=params)
            response.raise_for_status()
            page = unwrap(response)

            next_cursor = page.get("next_cursor")
            if next_cursor and next_cursor == cursor:
                raise RuntimeError(
                    "/products no avanza: el módulo devolvió el mismo next_cursor "
                    f"({next_cursor!r}) que se le envió, para storeId={store_id}. "
                    "Se aborta en vez de pedir la misma página indefinidamente."
                )

            yield page

            if not next_cursor:
                return
            cursor = next_cursor

    def iter_attributes(self, limit: int = 500) -> Iterator[dict]:
        """Recorre los atributos (con sus opciones y etiquetas) página a página.

        Mismo esquema que `iter_products`: cursor de paginación opaco, guarda de
        avance para que un `next_cursor` repetido aborte en vez de pedir la misma
        página para siempre, y `next_cursor: null` como única señal de fin.
        """
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = self._client.get("/attributes", params=params)
            response.raise_for_status()
            page = unwrap(response)

            next_cursor = page.get("next_cursor")
            if next_cursor and next_cursor == cursor:
                raise RuntimeError(
                    "/attributes no avanza: el módulo devolvió el mismo next_cursor "
                    f"({next_cursor!r}) que se le envió. Se aborta en vez de pedir "
                    "la misma página indefinidamente."
                )

            yield page

            if not next_cursor:
                return
            cursor = next_cursor

    def iter_categories(self, limit: int = 500) -> Iterator[dict]:
        """Recorre categorías, con su `path` y `store_states`, página a página.

        Mismo esquema que `iter_attributes`: cursor de paginación opaco, guarda
        de avance para que un `next_cursor` repetido aborte en vez de pedir la
        misma página para siempre, y `next_cursor: null` como única señal de fin.
        """
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = self._client.get("/categories", params=params)
            response.raise_for_status()
            page = unwrap(response)

            next_cursor = page.get("next_cursor")
            if next_cursor and next_cursor == cursor:
                raise RuntimeError(
                    "/categories no avanza: el módulo devolvió el mismo next_cursor "
                    f"({next_cursor!r}) que se le envió. Se aborta en vez de pedir "
                    "la misma página indefinidamente."
                )

            yield page

            if not next_cursor:
                return
            cursor = next_cursor

    def iter_deltas(
        self, since_id: int, limit: int = 1000, *, since_timestamp: int | None = None
    ) -> Iterator[dict]:
        """Recorre la cola de cambios desde un watermark, página a página.

        La guarda se evalúa ANTES del yield a propósito: quien consume escribe
        el watermark con el `last_change_id` de la página que aplicó, así que
        una página cuyo cursor no sirve no debe llegarle nunca.

        `since_timestamp` (Unix seconds) es la otra mitad del contrato de
        `/deltas`, la que hasta ahora nadie enviaba. Con Magento_Staging una
        actualización programada se vuelve ACTIVA cuando pasa su `created_in`,
        y en ese instante no ocurre ningún evento de Magento: el tiempo
        transcurre y ya. Un observer no puede suscribirse al paso del tiempo,
        así que la cola nunca se entera y el espejo serviría el valor viejo
        indefinidamente (la reconciliación tampoco lo ve: compara conjuntos de
        SKU, no valores). El módulo resuelve esa ventana en cada lectura y
        adjunta esos SKUs con `change_id: 0` — un centinela, porque la columna
        es `identity="true"` y MySQL arranca el AUTO_INCREMENT en 1.

        De ahí las dos reglas de abajo:

        1. Se envía SOLO en la primera petición. Las activaciones no se
           paginan por change_id: el módulo las adjunta a cada página que
           reciba el parámetro, así que pedirlas en todas sería reaplicar el
           mismo conjunto una vez por página.
        2. `last_change_id: null` con items deja de ser un error CUANDO todos
           los items son centinelas: significa que no hay filas de cola que
           paginar, solo activaciones. Se entrega la página (el consumidor
           necesita esos SKUs) y se termina, porque sin cursor la siguiente
           petición devolvería exactamente lo mismo para siempre. Con una fila
           real de cola presente, en cambio, sigue siendo el error que era: no
           hay con qué avanzar el watermark.
        """
        cursor = since_id
        first_request = True
        while True:
            params: dict[str, object] = {"sinceId": cursor, "limit": limit}
            if first_request and since_timestamp is not None:
                params["sinceTimestamp"] = since_timestamp
            response = self._client.get("/deltas", params=params)
            response.raise_for_status()
            page = unwrap(response)
            first_request = False

            if not page["items"]:
                return

            next_cursor = page.get("last_change_id")
            if next_cursor is None:
                queued = [item for item in page["items"] if item.get("change_id")]
                if queued:
                    raise RuntimeError(
                        "/deltas devolvió items de cola (change_id "
                        f"{[item['change_id'] for item in queued][:5]}) con "
                        f"last_change_id nulo desde sinceId={cursor}: no hay "
                        "cursor con el que avanzar el watermark, así que se "
                        "aborta en vez de fallar más tarde con un TypeError que "
                        "no explica nada."
                    )
                # Solo activaciones de versión (change_id 0). No hay cola que
                # paginar: se entrega esta página y se termina.
                yield page
                return
            if next_cursor <= cursor:
                raise RuntimeError(
                    "/deltas no avanza: devolvió items con "
                    f"last_change_id={next_cursor} desde sinceId={cursor}, que no "
                    "es mayor. Se aborta en vez de reaplicar la misma página "
                    "indefinidamente."
                )

            yield page
            cursor = next_cursor

    def products_by_sku(self, store_id: int, skus: list[str]) -> list[dict]:
        """Relee un conjunto concreto de SKUs. Complemento del endpoint de deltas:
        la cola dice QUÉ cambió, esto trae el estado nuevo.

        POST con cuerpo JSON y en lotes de `PRODUCTS_BY_SKU_CHUNK`, por dos
        motivos independientes:

        1. Los SKUs viajaban como `",".join(skus)` en la query string, así que
           un SKU que contiene una coma se partía en dos lookups y el producto
           real no se refrescaba nunca, en silencio. En esta rama la identidad
           es texto: 'ABC,123' es un SKU, no dos. Una lista JSON no tiene
           separador que colisione con el contenido.
        2. `delta_sync` pasa hasta 1000 SKUs de una vez: en un GET son unos
           20 KB de URL, que nginx rechaza con 414 mucho antes de que la
           petición llegue a PHP.

        Contrato del módulo:
            POST {base_url}/rest/V1/skudo/products-by-sku
            body: {"storeId": <int>, "skus": [<str>, ...]}   (<= 100 SKUs)
            200:  {"items": [ <mismo item que /products>, ... ]}
        """
        items: list[dict] = []
        for start in range(0, len(skus), PRODUCTS_BY_SKU_CHUNK):
            chunk = skus[start : start + PRODUCTS_BY_SKU_CHUNK]
            response = self._client.post(
                "/products-by-sku", json={"storeId": store_id, "skus": chunk}
            )
            response.raise_for_status()
            items.extend(unwrap(response)["items"])
        return items

    def checksums(self, store_id: int) -> dict:
        response = self._client.get("/checksums", params={"storeId": store_id})
        response.raise_for_status()
        return unwrap(response)

    def close(self) -> None:
        self._client.close()
