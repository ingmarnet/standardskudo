from datetime import UTC, datetime

from fastapi.testclient import TestClient

from skudo.findings.models import Finding, FindingRun
from skudo.mirror.models import Tenant
from skudo.rules.models import Rule
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'a@x.com', 'administrador')}"}


def test_rules_endpoint_lista_reglas_activas_con_recuento(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t)
    db_session.flush()
    r = Rule(
        tenant_id=t.id,
        scope_kind="attribute_set",
        scope_key="4",
        store_view_magento_id=1,
        axis=3,
        kind="obligatoriedad",
        definition={"attribute": "color"},
        confidence=0.97,
        evidence_count=100,
        exceptions=[],
        status="aceptada",
        origin="inferida",
    )
    db_session.add(r)
    db_session.flush()
    run = FindingRun(
        tenant_id=t.id,
        store_view_magento_id=1,
        mirror_sync_generation=1,
        product_count=10,
        ruleset_version=1,
        finished_at=datetime.now(UTC),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        Finding(
            run_id=run.id,
            code="regla:obligatoriedad:color",
            axis=3,
            severity="media",
            subject_type="producto",
            subject_key="B",
            evidence={"attribute": "color", "rule_id": r.id},
            rule_id=r.id,
            ruleset_version=1,
        )
    )
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/rules?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    data = resp.json()
    fila = next(x for x in data if x["attribute"] == "color")
    assert fila["kind"] == "obligatoriedad"
    assert fila["status"] == "aceptada"
    assert fila["origin"] == "inferida"
    assert fila["confidence"] == 0.97
    assert fila["productos_marcados"] == 1
