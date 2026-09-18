from fastapi.testclient import TestClient

from skudo.findings.models import FindingRun
from skudo.mirror.models import Tenant
from skudo.score.models import ProductScore
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'a@x.com', 'administrador')}"}


def test_products_devuelve_y_ordena_por_prioridad(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    # ProductScore.run_id referencia finding_run: hace falta una pasada real
    # antes de poder insertar las notas (FK), aunque el test no la use.
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                      mirror_sync_generation=1, product_count=2)
    db_session.add(run); db_session.flush()
    for sku, prio, puntaje in [("sinstock", "sin_stock", 90), ("pleno", "pleno", 90)]:
        db_session.add(ProductScore(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                    puntaje=puntaje, grado="A", critico=False,
                                    deducciones=[], prioridad_vitrina=prio, run_id=run.id))
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/products?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    prods = resp.json()["productos"]
    assert prods[0]["prioridad_vitrina"] == "pleno"       # pleno primero
    assert {p["prioridad_vitrina"] for p in prods} == {"pleno", "sin_stock"}
