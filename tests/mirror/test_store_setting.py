from datetime import UTC, datetime

from skudo.mirror.models import ProductSignal, StoreSetting, Tenant


def test_store_setting_unica_por_tenant_store_key(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                key="show_out_of_stock", value="false"))
    db_session.flush()
    fila = db_session.query(StoreSetting).filter_by(tenant_id=t.id, store_view_magento_id=1,
                                                    key="show_out_of_stock").one()
    assert fila.value == "false"


def test_product_signal_is_in_stock_admite_null(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(ProductSignal(tenant_id=t.id, sku="1007", store_view_magento_id=1,
                                 uses_msi=False, is_in_stock=None))
    db_session.flush()
    assert db_session.query(ProductSignal).filter_by(sku="1007").one().is_in_stock is None
