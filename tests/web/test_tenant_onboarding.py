from fastapi.testclient import TestClient

from skudo.mirror.models import Tenant
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(user_id=1, role="administrador", email="admin@x.com"):
    return {"Authorization": f"Bearer {create_token(user_id, email, role)}"}


def _tenant(db_session, code):
    t = Tenant(code=code, name=code.title(), base_url="http://x.test", token_env_var="X")
    db_session.add(t)
    db_session.flush()
    return t


def test_admin_crea_tenant_y_recibe_el_token_una_vez(db_session):
    client = _client(db_session)
    resp = client.post(
        "/api/tenants",
        json={"code": "renova", "name": "Renova", "base_url": "https://renova.com.py"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "renova"
    assert body["name"] == "Renova"
    assert body["token_env_var"] == "SKUDO_TENANT_RENOVA_TOKEN"
    assert isinstance(body["token"], str) and len(body["token"]) >= 32

    t = db_session.query(Tenant).filter_by(code="renova").one()
    assert t.base_url == "https://renova.com.py"


def test_lector_no_puede_crear_tenant(db_session):
    client = _client(db_session)
    resp = client.post(
        "/api/tenants",
        json={"code": "x", "name": "X", "base_url": "http://x.test"},
        headers=_auth(role="lector", email="l@x.com"),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 403
    assert db_session.query(Tenant).count() == 0


def test_crear_tenant_con_codigo_repetido_da_409(db_session):
    _tenant(db_session, "acme")
    client = _client(db_session)
    resp = client.post(
        "/api/tenants",
        json={"code": "acme", "name": "Acme", "base_url": "http://x.test"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 409
