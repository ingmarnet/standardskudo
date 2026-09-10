from collections.abc import Iterator

import httpx

from skudo.magento.environment import EnvironmentProfile, parse_environment

# Cuántos SKUs se piden por petición a /products-by-sku. `delta_sync` puede
# traer 1000 cambios en una página; mandarlos juntos es un cuerpo grande y una
# consulta `IN (...)` de 1000 elementos en el módulo. 100 mantiene las dos
# cosas en tamaños que Magento y Postgres manejan sin sorpresas.
PRODUCTS_BY_SKU_CHUNK = 100


class MagentoApiError(RuntimeError):
    """Error devuelto por el módulo, con el mensaje ya interpolado.

    B1: Magento no manda el mensaje de error armado. Manda la plantilla de
    `__()` y los argumentos por separado, para que quien lo reciba pueda
    traducirlo:

        {"message": "no se pueden pedir más de %1 SKUs por llamada (se
                     recibieron %2)",
         "parameters": [100, 101]}

    `raise_for_status()` de httpx no sabe nada de eso: su mensaje es
    "Client error '400 Bad Request' for url ...", así que el motivo real —el
    único dato útil— no aparecía en ningún log ni en ninguna traza. Esta
    clase interpola los `%1`/`%2` (lista) o los `%fieldName` (mapa) y expone
    `status_code` para que un caller pueda distinguir un 400 (entrada
    inválida: no reintentar) de un 5xx (fallo del servidor: reintentable).
    Esa distinción es justamente lo que M2 arregla del otro lado del cable.
    """

    def __init__(self, status_code: int, message: str, path: str):
        self.status_code = status_code
        self.message = message
        self.path = path
        super().__init__(f"{path} devolvió HTTP {status_code}: {message}")


def _interpolate(template: str, parameters: object) -> str:
    """Rellena los placeholders de `__()` de Magento.

    Dos formas, las dos observadas sobre HTTP real:
      - lista: `%1`, `%2`, … por posición (1-based).
      - mapa: `%fieldName` por nombre.
    Un placeholder sin valor se deja tal cual en vez de borrarse: un mensaje
    con un `%1` visible dice "falta un dato acá", que es información; uno con
    el hueco borrado miente sobre lo que el servidor dijo.
    """
    if isinstance(parameters, list):
        for index, value in enumerate(parameters, start=1):
            template = template.replace(f"%{index}", str(value))
        return template
    if isinstance(parameters, dict):
        # Las claves más largas primero: con `%field` y `%fieldName`, sustituir
        # la corta antes partiría la larga.
        for key in sorted(parameters, key=len, reverse=True):
            template = template.replace(f"%{key}", str(parameters[key]))
        return template
    return template


def raise_for_status(response: httpx.Response) -> None:
    """Reemplaza `response.raise_for_status()` en todo el cliente.

    Se llama incondicionalmente antes de leer cualquier cuerpo: un cuerpo de
    error no tiene la forma del payload y `unwrap()` fallaría con un mensaje
    sobre el envoltorio, que es la causa equivocada.
    """
    if not response.is_error:
        return

    path = response.request.url.path
    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict) and isinstance(body.get("message"), str):
        raise MagentoApiError(
            response.status_code,
            _interpolate(body["message"], body.get("parameters")),
            path,
        )

    # Sin cuerpo JSON con `message` (un 401 sin cuerpo, un 502 de un proxy):
    # se reporta el texto crudo acotado, no se inventa un motivo.
    raise MagentoApiError(response.status_code, response.text[:500], path)


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
        raise_for_status(response)
        return parse_environment(unwrap(response))

    def products_page(
        self, store_id: int, limit: int = 500, cursor: str | None = None
    ) -> dict:
        """UNA página del catálogo, desde el cursor que se le pase.

        Es la unidad que `full_sync` necesita desde H3: con commit por página
        y reanudación, quien recorre tiene que poder EMPEZAR en un cursor
        guardado, y un generador que siempre arranca en None no lo permite.
        `iter_products` se construye encima para quien quiera el recorrido
        entero.

        Con guarda de avance: un módulo que devuelve el mismo `next_cursor`
        que se le envió mantendría al llamador pidiendo la misma página para
        siempre. Un bucle infinito en un ingestor no se nota mientras pasa; se
        nota semanas después, como espejo desactualizado sin causa aparente.
        La guarda vive ACÁ y no en el bucle del llamador para que los tres
        caminos que paginan productos la tengan sin repetirla.
        """
        params: dict[str, object] = {"storeId": store_id, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        response = self._client.get("/products", params=params)
        raise_for_status(response)
        page = unwrap(response)

        next_cursor = page.get("next_cursor")
        if next_cursor and next_cursor == cursor:
            raise RuntimeError(
                "/products no avanza: el módulo devolvió el mismo next_cursor "
                f"({next_cursor!r}) que se le envió, para storeId={store_id}. "
                "Se aborta en vez de pedir la misma página indefinidamente."
            )
        return page

    def iter_products(
        self, store_id: int, limit: int = 500, cursor: str | None = None
    ) -> Iterator[dict]:
        """Recorre el catálogo página a página. Cada yield es una página completa."""
        while True:
            page = self.products_page(store_id, limit=limit, cursor=cursor)
            yield page
            next_cursor = page.get("next_cursor")
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
            raise_for_status(response)
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
            raise_for_status(response)
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
            raise_for_status(response)
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
            raise_for_status(response)
            items.extend(unwrap(response)["items"])
        return items

    def signals(self, store_id: int, days: int = 90) -> list[dict]:
        """Señales comerciales de una store view: ventas, stock físico vs.
        vendible, margen y demanda de búsqueda aproximada, en la ventana de
        `days`.

        Sin paginar, a diferencia de productos/atributos/categorías: el módulo
        solo devuelve los SKUs que tuvieron al menos una venta en la ventana
        (ver `SignalReader::salesRows()`), un subconjunto varios órdenes menor
        que el catálogo.

        Es por store view, nunca global: la señal comercial de un SKU en PY y
        en BR son hechos distintos, y es justo lo que permite priorizar los
        hallazgos por dinero de ESA tienda.

        Todo lo que el módulo no pudo medir llega como `null` y así debe
        quedarse: `revenue` nulo es "hay ítems de pedido sin importe" y
        `search_demand` nulo es "esta tienda no tiene datos de búsqueda", que
        no son lo mismo que cero.
        """
        response = self._client.get("/signals", params={"storeId": store_id, "days": days})
        raise_for_status(response)
        return unwrap(response)["items"]

    def checksums(self, store_id: int) -> dict:
        response = self._client.get("/checksums", params={"storeId": store_id})
        raise_for_status(response)
        return unwrap(response)

    def close(self) -> None:
        self._client.close()
