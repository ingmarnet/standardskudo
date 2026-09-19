"""Ingestor de nombres de attribute set.

El endpoint `/attribute-sets` del módulo lee `eav_attribute_set` (que el
espejo antes no traía: la tabla `attribute_set` quedaba vacía y un scope
`attribute_set:4` no tenía nombre). Esta pasada la puebla y, como el catálogo
de sets es chico y de una sola página, reemplaza el conjunto entero: upsert de
los presentes y borrado de los que el origen ya no ofrece.
"""

from types import SimpleNamespace

import httpx
from skudo_testing import skudo_response
from sqlalchemy import select

from skudo.ingest.attribute_set_sync import sync_attribute_sets
from skudo.ingest.source import TenantSource
from skudo.mirror.models import AttributeSet, Tenant

SETS_PAGE = {
    "items": [
        {"magento_id": 4, "name": "Default"},
        {"magento_id": 16, "name": "Calzado"},
    ]
}


def make_source(tenant_id: int, page: dict = SETS_PAGE) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/attribute-sets"):
            return skudo_response(page)
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


def _tenant(session, code="nissei"):
    row = Tenant(code=code, name=code.title(), base_url="https://x.test", token_env_var="T")
    session.add(row)
    session.flush()
    return row


def test_sync_puebla_los_nombres_de_los_sets(db_session):
    t = _tenant(db_session)
    n = sync_attribute_sets(db_session, make_source(t.id))
    assert n == 2
    nombres = {
        mid: name
        for mid, name in db_session.execute(
            select(AttributeSet.magento_id, AttributeSet.name).where(
                AttributeSet.tenant_id == t.id
            )
        ).all()
    }
    assert nombres == {4: "Default", 16: "Calzado"}


def test_sync_actualiza_y_barre_los_que_el_origen_ya_no_ofrece(db_session):
    t = _tenant(db_session)
    # un set viejo (99) y uno que cambiará de nombre (16)
    db_session.add(AttributeSet(tenant_id=t.id, magento_id=99, name="Viejo"))
    db_session.add(AttributeSet(tenant_id=t.id, magento_id=16, name="Nombre viejo"))
    db_session.flush()

    sync_attribute_sets(db_session, make_source(t.id))

    nombres = {
        mid: name
        for mid, name in db_session.execute(
            select(AttributeSet.magento_id, AttributeSet.name).where(
                AttributeSet.tenant_id == t.id
            )
        ).all()
    }
    assert nombres == {4: "Default", 16: "Calzado"}  # 99 barrido, 16 actualizado


def test_sync_no_toca_los_sets_de_otro_tenant(db_session):
    a = _tenant(db_session, "acme")
    b = _tenant(db_session, "beta")
    db_session.add(AttributeSet(tenant_id=b.id, magento_id=7, name="De beta"))
    db_session.flush()

    sync_attribute_sets(db_session, make_source(a.id))

    # los sets de beta siguen intactos
    beta = db_session.scalars(
        select(AttributeSet).where(AttributeSet.tenant_id == b.id)
    ).all()
    assert len(beta) == 1 and beta[0].name == "De beta"
