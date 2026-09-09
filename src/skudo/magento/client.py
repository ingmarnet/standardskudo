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
        """Recorre el catálogo página a página. Cada yield es una página completa."""
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"storeId": store_id, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = self._client.get("/products", params=params)
            response.raise_for_status()
            page = response.json()

            yield page

            cursor = page.get("next_cursor")
            if not cursor:
                return

    def iter_deltas(self, since_id: int, limit: int = 1000) -> Iterator[dict]:
        """Recorre la cola de cambios desde un watermark, página a página."""
        cursor = since_id
        while True:
            response = self._client.get("/deltas", params={"sinceId": cursor, "limit": limit})
            response.raise_for_status()
            page = response.json()

            if not page["items"]:
                return

            yield page
            cursor = page["last_change_id"]

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

    def close(self) -> None:
        self._client.close()
