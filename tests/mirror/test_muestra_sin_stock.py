from skudo.mirror.models import StoreSetting, Tenant
from skudo.mirror.store_settings import muestra_sin_stock


def test_none_si_no_hay_config(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    assert muestra_sin_stock(db_session, t.id, 1) is None


def test_true_y_false_segun_valor(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                key="show_out_of_stock", value="true"))
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=2,
                                key="show_out_of_stock", value="false"))
    db_session.flush()
    assert muestra_sin_stock(db_session, t.id, 1) is True
    assert muestra_sin_stock(db_session, t.id, 2) is False
