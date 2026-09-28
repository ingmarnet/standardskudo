from fastapi.testclient import TestClient
from sqlalchemy import select

from skudo.config import tenant_token
from skudo.mirror.models import Tenant
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(user_id=1, role="administrador", email="admin@x.com"):
    return {"Authorization": f"Bearer {create_token(user_id, email, role)}"}


def test_onboarding_persiste_token_en_archivo(db_session, _tenant_token_dir):
    client = _client(db_session)
    resp = client.post(
        "/api/tenants",
        json={"code": "acme", "name": "Acme", "base_url": "https://a.test"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 201
    token = resp.json()["token"]
    # el token vive en un archivo por tenant, NO en la base ni en el entorno
    assert (_tenant_token_dir / "acme").read_text() == token
    tenant = db_session.scalar(select(Tenant).where(Tenant.code == "acme"))
    assert tenant_token(tenant) == token  # el ingestor lo lee del archivo


def test_tenant_token_sin_archivo_lee_la_env_var(db_session, _tenant_token_dir, monkeypatch):
    monkeypatch.setenv("SKUDO_TENANT_LEGACY_TOKEN", "legacy-secret")
    t = Tenant(
        code="legacy",
        name="Legacy",
        base_url="http://x.test",
        token_env_var="SKUDO_TENANT_LEGACY_TOKEN",
    )
    db_session.add(t)
    db_session.flush()
    assert tenant_token(t) == "legacy-secret"
