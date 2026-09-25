from fastapi.testclient import TestClient

from skudo.auth.models import PlatformUser
from skudo.auth.users import authenticate
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


def test_administrador_crea_usuario_con_acceso_a_tenants(db_session):
    acme = _tenant(db_session, "acme")
    beta = _tenant(db_session, "beta")
    client = _client(db_session)

    resp = client.post(
        "/api/users",
        json={
            "email": "Nuevo@Ejemplo.COM",
            "password": "secreta123",
            "role": "lector",
            "tenant_ids": [beta.id, acme.id],
        },
        headers=_auth(),
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "nuevo@ejemplo.com"
    assert body["role"] == "lector"
    assert body["tenant_ids"] == [acme.id, beta.id]
    assert "password" not in body
    assert authenticate(db_session, "nuevo@ejemplo.com", "secreta123") is not None


def test_usuario_solo_ve_tenants_asignados(db_session):
    _tenant(db_session, "acme")
    beta = _tenant(db_session, "beta")
    client = _client(db_session)

    creado = client.post(
        "/api/users",
        json={
            "email": "lector@ejemplo.com",
            "password": "secreta123",
            "role": "lector",
            "tenant_ids": [beta.id],
        },
        headers=_auth(),
    )
    assert creado.status_code == 201
    user_id = creado.json()["id"]

    listado = client.get(
        "/api/tenants",
        headers=_auth(user_id=user_id, role="lector", email="lector@ejemplo.com"),
    )
    assert listado.status_code == 200
    assert [t["code"] for t in listado.json()] == ["beta"]

    permitido = client.get(
        "/api/tenants/beta/health?store=1",
        headers=_auth(user_id=user_id, role="lector", email="lector@ejemplo.com"),
    )
    denegado = client.get(
        "/api/tenants/acme/health?store=1",
        headers=_auth(user_id=user_id, role="lector", email="lector@ejemplo.com"),
    )
    app.dependency_overrides.clear()
    assert permitido.status_code == 200
    assert denegado.status_code == 403


def test_lector_no_puede_crear_usuarios(db_session):
    _tenant(db_session, "acme")
    client = _client(db_session)
    resp = client.post(
        "/api/users",
        json={"email": "x@y.com", "password": "secreta123", "role": "lector", "tenant_ids": []},
        headers=_auth(role="lector", email="lector@x.com"),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 403
    assert db_session.query(PlatformUser).count() == 0
