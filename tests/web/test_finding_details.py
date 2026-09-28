"""El detalle de hallazgos enriquece con nombre y stock, priorizando con stock."""

from fastapi.testclient import TestClient

from skudo.findings.models import Finding, FindingRun
from skudo.mirror.models import ProductRecord, ProductSignal, Tenant
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth(user_id=1, role="administrador", email="admin@x.com"):
    return {"Authorization": f"Bearer {create_token(user_id, email, role)}"}


def _tenant(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t)
    db_session.flush()
    return t


def _run(db_session, tenant):
    run = FindingRun(
        tenant_id=tenant.id, store_view_magento_id=1,
        mirror_sync_generation=1, product_count=3,
    )
    db_session.add(run)
    db_session.flush()
    return run


def _finding(db_session, run, sku, code="sin_categoria"):
    db_session.add(Finding(
        run_id=run.id, code=code, axis=2, severity="media",
        subject_type="producto", subject_key=sku, evidence={},
    ))
    db_session.flush()


def _producto(db_session, tenant, sku, nombre):
    db_session.add(ProductRecord(
        tenant_id=tenant.id, sku=sku, store_view_magento_id=1,
        attributes={"name": nombre}, attribute_set_id=4, type_id="simple",
        sync_generation=1, scope_provenance={}, content_hash=sku,
    ))
    db_session.flush()


def _stock(db_session, tenant, sku, is_in_stock):
    db_session.add(ProductSignal(
        tenant_id=tenant.id, sku=sku, store_view_magento_id=1,
        uses_msi=False, is_in_stock=is_in_stock,
    ))
    db_session.flush()


def test_detalle_incluye_nombre_y_stock(db_session):
    t = _tenant(db_session)
    run = _run(db_session, t)
    _finding(db_session, run, "SKU-A")
    _finding(db_session, run, "SKU-B")
    _producto(db_session, t, "SKU-A", "Aire Acondicionado Inverter")
    _producto(db_session, t, "SKU-B", "Ventilador de pie")
    _stock(db_session, t, "SKU-A", True)
    _stock(db_session, t, "SKU-B", False)

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/findings/sin_categoria?store=1", headers=_auth())
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    por_sku = {i["subject_key"]: i for i in items}
    assert por_sku["SKU-A"]["name"] == "Aire Acondicionado Inverter"
    assert por_sku["SKU-A"]["is_in_stock"] is True
    assert por_sku["SKU-B"]["name"] == "Ventilador de pie"
    assert por_sku["SKU-B"]["is_in_stock"] is False


def test_detalle_prioriza_con_stock(db_session):
    t = _tenant(db_session)
    run = _run(db_session, t)
    _finding(db_session, run, "SIN-STOCK")
    _finding(db_session, run, "CON-STOCK")
    _producto(db_session, t, "SIN-STOCK", "Producto sin stock")
    _producto(db_session, t, "CON-STOCK", "Producto con stock")
    _stock(db_session, t, "SIN-STOCK", False)
    _stock(db_session, t, "CON-STOCK", True)

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/findings/sin_categoria?store=1", headers=_auth())
    app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["subject_key"] for i in items] == ["CON-STOCK", "SIN-STOCK"]
