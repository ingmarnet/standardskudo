import httpx

from skudo.magento.client import MagentoClient
from skudo.mirror.models import Tenant


class TenantSource:
    """Ata un tenant al Magento del que se lee su catálogo.

    Las funciones de sincronización recibían `(client, tenant_id)` como dos
    argumentos independientes. Eso leía fielmente el catálogo del tenant A —el
    cliente lleva su `base_url` y su token— y lo escribía en el espejo del
    tenant B si alguien pasaba el id de B. Ninguna constraint, tipo ni aserción
    lo atrapaba, y el resultado es el peor fallo posible en un multi-tenant: el
    catálogo de un cliente dentro del espejo de otro.

    Aquí el id del tenant y las credenciales con las que se lee su Magento son
    un solo objeto, así que desemparejarlos deja de ser una llamada expresable.

    El token no se guarda como atributo público ni aparece en el `repr`: ese
    repr acaba en logs y en trazas de tests que fallan.
    """

    def __init__(
        self,
        tenant_id: int,
        base_url: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.tenant_id = tenant_id
        self.base_url = base_url
        self._token = token
        self._transport = transport
        self._client: MagentoClient | None = None

    @classmethod
    def from_tenant(
        cls, tenant: Tenant, token: str, transport: httpx.BaseTransport | None = None
    ) -> "TenantSource":
        """El token se pasa aparte porque no vive en la base: la fila del tenant
        solo guarda el NOMBRE de su variable de entorno."""
        return cls(tenant.id, tenant.base_url, token, transport=transport)

    @property
    def client(self) -> MagentoClient:
        """Construido a demanda y reutilizado, para que una sincronización no
        abra una conexión nueva por endpoint."""
        if self._client is None:
            self._client = MagentoClient(
                self.base_url, self._token, transport=self._transport
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __repr__(self) -> str:
        return f"TenantSource(tenant_id={self.tenant_id}, base_url={self.base_url!r})"
