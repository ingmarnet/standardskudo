import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from skudo.audit.models import AuditLog
from skudo.mirror.models import Tenant
from skudo.rules.models import Rule
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


def _rule(db_session, tenant, *, status="borrador"):
    r = Rule(
        tenant_id=tenant.id,
        scope_kind="attribute_set",
        scope_key="4",
        store_view_magento_id=1,
        axis=3,
        kind="obligatoriedad",
        definition={"attribute": "color", "marcaria": 10},
        confidence=0.95,
        evidence_count=100,
        exceptions=[],
        status=status,
        origin="inferida",
    )
    db_session.add(r)
    db_session.flush()
    return r


def test_aceptar_regla_registra_auditoria(db_session):
    t = _tenant(db_session, "acme")
    r = _rule(db_session, t)
    client = _client(db_session)
    resp = client.post(f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    entradas = db_session.scalars(select(AuditLog)).all()
    assert len(entradas) == 1
    e = entradas[0]
    assert e.action == "rule.accept"
    assert e.actor_email == "admin@x.com"
    assert e.tenant_id == t.id
    assert e.entity_type == "rule"
    assert e.entity_id == r.id


def test_crear_tenant_y_usuario_registran_auditoria(db_session):
    client = _client(db_session)
    r1 = client.post(
        "/api/tenants",
        json={"code": "acme", "name": "Acme", "base_url": "https://a.test"},
        headers=_auth(),
    )
    r2 = client.post(
        "/api/users",
        json={"email": "u@x.com", "password": "secreta123", "role": "lector", "tenant_roles": []},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert r1.status_code == 201
    assert r2.status_code == 201
    acciones = sorted(e.action for e in db_session.scalars(select(AuditLog)).all())
    assert acciones == ["tenant.create", "user.create"]


def test_audit_es_inmutable(db_session):
    t = _tenant(db_session, "acme")
    r = _rule(db_session, t)
    client = _client(db_session)
    client.post(f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth())
    app.dependency_overrides.clear()
    entrada = db_session.scalars(select(AuditLog)).one()
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        db_session.execute(
            text("UPDATE audit_log SET actor_email='tampered@x.com' WHERE id=:id"),
            {"id": entrada.id},
        )


def test_listar_auditoria_solo_admin(db_session):
    t = _tenant(db_session, "acme")
    r = _rule(db_session, t)
    client = _client(db_session)
    client.post(f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth())
    ok = client.get("/api/audit", headers=_auth())
    denegado = client.get("/api/audit", headers=_auth(role="lector", email="l@x.com"))
    filtrado = client.get("/api/audit?tenant_code=acme", headers=_auth())
    inexistente = client.get("/api/audit?tenant_code=noexiste", headers=_auth())
    app.dependency_overrides.clear()
    assert ok.status_code == 200
    assert len(ok.json()) == 1
    assert ok.json()[0]["action"] == "rule.accept"
    assert denegado.status_code == 403
    assert len(filtrado.json()) == 1
    assert inexistente.status_code == 404
