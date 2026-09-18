"""Endpoints de creación manual de reglas desde el panel.

POST /rules crea una regla `curada` en borrador (rol de curación). GET
/rules/opciones puebla los desplegables del formulario con atributos y sets
reales del tenant.
"""

from fastapi.testclient import TestClient

from skudo.mirror.models import Attribute, ProductRecord, Tenant
from skudo.rules.models import Rule
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(role="administrador", email="a@x.com"):
    return {"Authorization": f"Bearer {create_token(1, email, role)}"}


def _tenant(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t)
    db_session.flush()
    return t


def test_crear_obligatoriedad_desde_el_panel(db_session):
    t = _tenant(db_session)
    client = _client(db_session)
    resp = client.post(
        "/api/tenants/acme/rules?store=1",
        json={"kind": "obligatoriedad", "attribute": "color",
              "scope_kind": "attribute_set", "scope_key": "4"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "borrador"
    assert body["origin"] == "curada"
    assert body["attribute"] == "color"
    assert db_session.query(Rule).filter_by(tenant_id=t.id).count() == 1


def test_crear_rango_invalido_da_400(db_session):
    _tenant(db_session)
    client = _client(db_session)
    resp = client.post(
        "/api/tenants/acme/rules?store=1",
        json={"kind": "rango", "attribute": "peso", "scope_kind": "global",
              "minimo": 5, "maximo": 5},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 400


def test_crear_duplicado_da_409(db_session):
    _tenant(db_session)
    client = _client(db_session)
    payload = {"kind": "obligatoriedad", "attribute": "color",
               "scope_kind": "attribute_set", "scope_key": "4"}
    r1 = client.post("/api/tenants/acme/rules?store=1", json=payload, headers=_auth())
    r2 = client.post("/api/tenants/acme/rules?store=1", json=payload, headers=_auth())
    app.dependency_overrides.clear()
    assert r1.status_code == 200
    assert r2.status_code == 409


def test_lector_no_puede_crear(db_session):
    _tenant(db_session)
    client = _client(db_session)
    resp = client.post(
        "/api/tenants/acme/rules?store=1",
        json={"kind": "obligatoriedad", "attribute": "color", "scope_kind": "global"},
        headers=_auth(role="lector"),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_opciones_lista_atributos_y_sets(db_session):
    t = _tenant(db_session)
    db_session.add(Attribute(tenant_id=t.id, code="color", label="Color",
                             frontend_input="select", declared_scope="global",
                             is_filterable=True, is_required=False, attribute_set_ids=[4]))
    db_session.add(Attribute(tenant_id=t.id, code="peso", label="Peso",
                             frontend_input="text", declared_scope="global",
                             is_filterable=False, is_required=False, attribute_set_ids=[4, 9]))
    for sku, sid in [("A", 4), ("B", 9)]:
        db_session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                     attributes={}, attribute_set_id=sid, type_id="simple",
                                     sync_generation=1, scope_provenance={}, content_hash=sku))
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/rules/opciones?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert [a["code"] for a in body["atributos"]] == ["color", "peso"]
    assert body["sets"] == [4, 9]
