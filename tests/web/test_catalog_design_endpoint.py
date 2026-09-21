"""Endpoint del Eje 11 (diseño del catálogo)."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from skudo.mirror.models import Attribute, ProductRecord, Tenant
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(role="lector"):
    return {"Authorization": f"Bearer {create_token(1, 'a@x.com', role)}"}


def _tenant(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    return t


def test_sin_perfil_devuelve_solo_sets_muertos(db_session):
    t = _tenant(db_session)
    db_session.add(Attribute(tenant_id=t.id, code="color", label="Color",
                             frontend_input="select", declared_scope="global",
                             is_filterable=True, is_required=False, attribute_set_ids=[4, 9]))
    db_session.add(ProductRecord(tenant_id=t.id, sku="A", store_view_magento_id=1,
                                 attributes={}, attribute_set_id=4, type_id="simple",
                                 sync_generation=1, scope_provenance={}, content_hash="A"))
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/catalog-design?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["perfil"] is None
    assert body["sets_muertos"] == [{"magento_id": 9, "name": None}]
    assert body["filtros_inutiles"] == [] and body["filtros_perdidos"] == []


def test_con_perfil_devuelve_filtros_perdidos(db_session):
    t = _tenant(db_session)
    db_session.add(Attribute(tenant_id=t.id, code="material", label="Material",
                             frontend_input="select", declared_scope="global",
                             is_filterable=False, is_required=False, attribute_set_ids=[4]))
    db_session.add(ProductRecord(tenant_id=t.id, sku="A", store_view_magento_id=1,
                                 attributes={}, attribute_set_id=4, type_id="simple",
                                 sync_generation=1, scope_provenance={}, content_hash="A"))
    run = ProfileRun(tenant_id=t.id, store_view_magento_id=1, mirror_sync_generation=1,
                     thresholds={}, product_count=100, finished_at=datetime.now(UTC))
    db_session.add(run); db_session.flush()
    part = ProfilePartition(run_id=run.id, attribute_set_id=4, splitter_kind="set",
                            splitter_key=None, splitter_value=None, product_count=100,
                            decision_reason="elegido")
    db_session.add(part); db_session.flush()
    db_session.add(AttributeCoverage(partition_id=part.id, attribute_code="material",
                                     presente=95, vacio=5, no_aplica=0, desconocido=0,
                                     coverage=0.95))
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/catalog-design?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["perfil"] == run.id
    assert [f["attribute"] for f in body["filtros_perdidos"]] == ["material"]
