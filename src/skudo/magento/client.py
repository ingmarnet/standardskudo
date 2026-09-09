from collections.abc import Iterator

import httpx

from skudo.magento.environment import EnvironmentProfile, parse_environment


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
        return parse_environment(response.json())

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
            page = response.json()

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

    def iter_deltas(self, since_id: int, limit: int = 1000) -> Iterator[dict]:
        """Recorre la cola de cambios desde un watermark, página a página.

        La guarda se evalúa ANTES del yield a propósito: quien consume escribe
        el watermark con el `last_change_id` de la página que aplicó, así que
        una página cuyo cursor no sirve no debe llegarle nunca.
        """
        cursor = since_id
        while True:
            response = self._client.get("/deltas", params={"sinceId": cursor, "limit": limit})
            response.raise_for_status()
            page = response.json()

            if not page["items"]:
                return

            next_cursor = page.get("last_change_id")
            if next_cursor is None:
                raise RuntimeError(
                    "/deltas devolvió items con last_change_id nulo desde "
                    f"sinceId={cursor}: no hay cursor con el que avanzar el "
                    "watermark, así que se aborta en vez de fallar más tarde con "
                    "un TypeError que no explica nada."
                )
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
        la cola dice QUÉ cambió, esto trae el estado nuevo."""
        if not skus:
            return []
        response = self._client.get(
            "/products-by-sku", params={"storeId": store_id, "skus": ",".join(skus)}
        )
        response.raise_for_status()
        return response.json()["items"]

    def checksums(self, store_id: int) -> dict:
        response = self._client.get("/checksums", params={"storeId": store_id})
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
