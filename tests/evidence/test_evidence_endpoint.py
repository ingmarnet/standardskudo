"""S2 — la API expone la evidencia (y el conflicto) a quien aprueba."""

from fastapi.testclient import TestClient

from skudo.evidence.service import record
from skudo.mirror.models import Tenant
from skudo.rules.models import Rule
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(user_id=1, role="administrador", email="admin@x.com"):
    return {"Authorization": f"Bearer {create_token(user_id, email, role)}"}


def _tenant(session, code="acme"):
    t = Tenant(code=code, name=code.title(), base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _rule(session, tenant):
    r = Rule(
        tenant_id=tenant.id, scope_kind="attribute_set", scope_key="4",
        store_view_magento_id=1, axis=3, kind="rango",
        definition={"attribute": "peso", "min": 0.5, "max": 8.0},
        confidence=0.99, evidence_count=200, exceptions=[], status="borrador",
        origin="inferida",
    )
    session.add(r)
    session.flush()
    return r


def test_endpoint_devuelve_procedencia_y_conflicto(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t)
    record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=r.id,
        datum="rango", source="perfil", fragment="perfil 1 ...", value={"min": 0.5, "max": 8.0},
    )
    record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=r.id,
        datum="rango", source="canal", fragment="ficha fabricante", value={"min": 0.5, "max": 12.0},
    )
    db_session.flush()

    client = _client(db_session)
    resp = client.get(f"/api/tenants/acme/rules/{r.id}/evidence", headers=_auth())
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["evidence"]) == 2
    assert len(body["conflicts"]) == 1
    assert len(body["conflicts"][0]) == 2
    for e in body["evidence"]:
        assert "source" in e and "fragment" in e and "transformation" in e


def test_evidencia_de_regla_de_otro_tenant_da_404(db_session):
    t = _tenant(db_session, code="acme")
    otra = _tenant(db_session, code="otra")
    r_otra = _rule(db_session, otra)
    client = _client(db_session)
    resp = client.get(f"/api/tenants/acme/rules/{r_otra.id}/evidence", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 404, "la evidencia de un tenant no se filtra a otro"
