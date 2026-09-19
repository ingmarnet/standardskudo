"""set-info: identificar un attribute set sin depender del nombre de Magento.

El nombre real (eav_attribute_set) todavía no se sincroniza; hasta que llegue,
el set se identifica por su HUELLA: cuántos productos tiene y sus atributos más
presentes (sin los de sistema). Cuando el nombre se sincronice, se muestra.
"""

from fastapi.testclient import TestClient

from skudo.mirror.models import AttributeSet, ProductRecord, Tenant
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'a@x.com', 'lector')}"}


def _prod(session, t, sku, sid, attrs):
    session.add(ProductRecord(
        tenant_id=t.id, sku=sku, store_view_magento_id=1,
        attributes={"status": "1", "visibility": "4", **attrs},
        attribute_set_id=sid, type_id="simple", sync_generation=1,
        scope_provenance={}, content_hash=sku))


def test_set_info_da_conteo_y_atributos_distintivos(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    # set 16: calzado, con color/talla
    _prod(db_session, t, "A", 16, {"color": "rojo", "talla": "42"})
    _prod(db_session, t, "B", 16, {"color": "azul", "talla": "40"})
    # set 4: otro, sin esos atributos
    _prod(db_session, t, "C", 4, {"material_mochila": "nylon"})
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/rules/set-info?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    info = resp.json()
    assert info["16"]["productos"] == 2
    assert info["4"]["productos"] == 1
    # atributos distintivos, sin los de sistema (status/visibility no aparecen)
    assert "color" in info["16"]["atributos"]
    assert "talla" in info["16"]["atributos"]
    assert "status" not in info["16"]["atributos"]
    assert "visibility" not in info["16"]["atributos"]
    # nombre real: aún no sincronizado -> null
    assert info["16"]["name"] is None


def test_set_info_usa_el_nombre_real_si_existe(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(AttributeSet(tenant_id=t.id, magento_id=16, name="Calzado"))
    _prod(db_session, t, "A", 16, {"color": "rojo"})
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/rules/set-info?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.json()["16"]["name"] == "Calzado"
