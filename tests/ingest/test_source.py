"""El objeto que ata un tenant al Magento del que se lee su catálogo.

`full_sync(session, client, tenant_id, ...)` leía fielmente el catálogo del
tenant A —el cliente lleva su base_url y su token— y lo escribía en el espejo
del tenant B si alguien pasaba el id de B. No había constraint, tipo ni
aserción que lo atrapara, y el resultado sería el peor fallo posible en un
multi-tenant: el catálogo de un cliente dentro del espejo de otro. Todavía no
hay llamador de producción, así que es el momento de hacerlo imposible.
"""

import inspect
from types import SimpleNamespace

import httpx
import pytest

from skudo.ingest.delta_sync import delta_sync
from skudo.ingest.full_sync import full_sync
from skudo.ingest.reconcile import reconcile
from skudo.ingest.source import TenantSource
from skudo.magento.client import MagentoClient
from skudo.mirror.models import Tenant


def _row(tenant_id: int, base_url: str):
    """Un objeto con `.id`/`.base_url`, tal como `from_tenant` los lee de un
    `Tenant`, sin necesitar sesión de base de datos para estos tests."""
    return SimpleNamespace(id=tenant_id, base_url=base_url)


def test_the_sync_entry_points_take_no_separate_tenant_id():
    """La firma es la garantía. Mientras el id del tenant y el cliente sean dos
    argumentos independientes, desemparejarlos es una llamada válida."""
    for function, expected in (
        # `full_sync` lleva además `page_size` y `restart` (H3), que son
        # opciones de la pasada y no identidad: lo que esta prueba vigila es
        # que el tenant no vuelva a viajar separado del cliente, así que se
        # comparan los parámetros POSICIONALES.
        (full_sync, ["session", "source", "store_view_ids"]),
        (delta_sync, ["session", "source", "store_view_ids"]),
        (reconcile, ["session", "source", "store_view_magento_id"]),
    ):
        positional = [
            name
            for name, parameter in inspect.signature(function).parameters.items()
            if parameter.kind is not inspect.Parameter.KEYWORD_ONLY
        ]
        assert positional == expected


def test_the_source_carries_the_tenant_and_builds_its_own_client():
    source = TenantSource.from_tenant(_row(7, "https://a.test"), token="secreto")

    assert source.tenant_id == 7
    assert isinstance(source.client, MagentoClient)
    assert str(source.client._client.base_url) == "https://a.test/rest/V1/skudo/"


def test_the_client_is_built_once_and_reused():
    source = TenantSource.from_tenant(_row(7, "https://a.test"), token="secreto")

    assert source.client is source.client


def test_the_source_is_built_from_the_tenant_row(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://n.test",
                 token_env_var="SKUDO_TENANT_NISSEI_TOKEN")
    db_session.add(row)
    db_session.flush()

    source = TenantSource.from_tenant(row, token="secreto")

    assert source.tenant_id == row.id
    assert source.base_url == "https://n.test"


def test_the_token_never_shows_up_in_the_repr():
    """El repr acaba en logs y en trazas de tests que fallan."""
    source = TenantSource.from_tenant(_row(7, "https://a.test"), token="secreto")

    assert "secreto" not in repr(source)
    assert "7" in repr(source)


def test_closing_the_source_closes_its_client():
    source = TenantSource.from_tenant(
        _row(7, "https://a.test"), token="t",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    client = source.client

    source.close()

    assert client._client.is_closed


def test_direct_construction_is_rejected_even_with_a_matched_pair():
    """`from_tenant()` es la única vía pública. Si esta guarda se borrara, la
    construcción directa —aquí con un par emparejado, no cruzado— dejaría de
    lanzar y este test lo detectaría."""
    with pytest.raises(TypeError, match="from_tenant"):
        TenantSource(tenant_id=7, base_url="https://a.test", token="secreto")


def test_a_crossed_tenant_and_credentials_pair_cannot_be_constructed():
    """El caso que de verdad importa: alguien intenta atar el id del tenant B
    a la base_url y el token del tenant A. Si `__init__` aceptara los tres por
    separado, esta construcción tendría éxito y produciría exactamente el peor
    fallo posible en un multi-tenant: el catálogo de A escribiéndose en el
    espejo de B, sin ningún error en ningún lado."""
    tenant_a_url = "https://a.test"
    tenant_a_token = "token-de-a"
    tenant_b_id = 99

    with pytest.raises(TypeError, match="from_tenant"):
        TenantSource(tenant_id=tenant_b_id, base_url=tenant_a_url, token=tenant_a_token)
