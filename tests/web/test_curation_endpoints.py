"""Endpoints de curación: aceptar / acotar / rechazar / snapshot desde el panel.

Envuelven las funciones puras de `skudo.rules.curation` y delegan la validez de
cada transición en la máquina de estados. Acá se prueba lo que agrega la capa
web: control de rol, pertenencia al tenant y el mapeo de las excepciones de
curación a códigos HTTP.
"""

from fastapi.testclient import TestClient

from skudo.mirror.models import Tenant
from skudo.rules.models import Rule
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(role="administrador", email="a@x.com"):
    return {"Authorization": f"Bearer {create_token(1, email, role)}"}


def _tenant(db_session, code="acme"):
    t = Tenant(code=code, name=code.title(), base_url="http://x.test", token_env_var="X")
    db_session.add(t)
    db_session.flush()
    return t


def _rule(db_session, tenant, *, status="borrador", origin="inferida", definition=None):
    r = Rule(
        tenant_id=tenant.id,
        scope_kind="attribute_set",
        scope_key="4",
        store_view_magento_id=1,
        axis=3,
        kind="obligatoriedad",
        definition=definition or {"attribute": "color", "marcaria": 10},
        confidence=0.95,
        evidence_count=100,
        exceptions=[],
        status=status,
        origin=origin,
    )
    db_session.add(r)
    db_session.flush()
    return r


def test_aceptar_pasa_borrador_a_aceptada(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t)
    client = _client(db_session)
    resp = client.post(f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "aceptada"
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_aceptar_ambigua_sin_confirmar_pide_confirmacion(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t, definition={"attribute": "color", "marcaria": 10, "ambiguo": True})
    client = _client(db_session)
    resp = client.post(f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth())
    assert resp.status_code == 409
    assert resp.json()["detail"]["needs_confirm"] is True
    db_session.refresh(r)
    assert r.status == "borrador"  # no se movió

    resp2 = client.post(
        f"/api/tenants/acme/rules/{r.id}/accept",
        json={"confirmar_ambiguo": True},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp2.status_code == 200
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_rechazar_exige_motivo(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t)
    client = _client(db_session)
    resp = client.post(
        f"/api/tenants/acme/rules/{r.id}/reject",
        json={"motivo": "   "},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 400
    db_session.refresh(r)
    assert r.status == "borrador"


def test_rechazar_con_motivo_pasa_a_rechazada(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t)
    client = _client(db_session)
    resp = client.post(
        f"/api/tenants/acme/rules/{r.id}/reject",
        json={"motivo": "no aplica a este catálogo"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "rechazada"


def test_no_se_puede_rechazar_un_piso_externo(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t, status="aceptada", origin="piso_externo")
    client = _client(db_session)
    resp = client.post(
        f"/api/tenants/acme/rules/{r.id}/reject",
        json={"motivo": "intento indebido"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 409
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_acotar_piso_externo_a_aviso(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t, status="aceptada", origin="piso_externo")
    client = _client(db_session)
    resp = client.post(
        f"/api/tenants/acme/rules/{r.id}/limit",
        json={"motivo": "demasiados falsos positivos"},
        headers=_auth(),
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["status"] == "aviso"


def test_lector_no_puede_curar(db_session):
    t = _tenant(db_session)
    r = _rule(db_session, t)
    client = _client(db_session)
    resp = client.post(
        f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth(role="lector")
    )
    app.dependency_overrides.clear()
    assert resp.status_code == 403
    db_session.refresh(r)
    assert r.status == "borrador"


def test_no_se_cura_regla_de_otro_tenant(db_session):
    a = _tenant(db_session, code="acme")
    b = _tenant(db_session, code="beta")
    r = _rule(db_session, b)  # regla del tenant beta
    client = _client(db_session)
    resp = client.post(f"/api/tenants/acme/rules/{r.id}/accept", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 404
    db_session.refresh(r)
    assert r.status == "borrador"


def test_snapshot_congela_las_activas(db_session):
    t = _tenant(db_session)
    _rule(db_session, t, status="aceptada")
    _rule(db_session, t, status="aviso")
    _rule(db_session, t, status="borrador")  # no debe entrar
    client = _client(db_session)
    resp = client.post("/api/tenants/acme/rules/snapshot?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["rule_count"] == 2
