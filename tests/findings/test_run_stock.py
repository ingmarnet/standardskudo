from skudo.findings.models import Finding
from skudo.findings.run import detect_store_view
from skudo.mirror.models import ProductRecord, ProductSignal, StoreSetting, Tenant


def _setup(session, *, is_in_stock, muestra):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t); session.flush()
    # producto publicado SIN descripción (generaría hallazgo) — el stock decide.
    session.add(ProductRecord(tenant_id=t.id, sku="1007", store_view_magento_id=1,
                              attributes={"status": "1", "visibility": "4"},
                              attribute_set_id=4, type_id="simple",
                              sync_generation=1, scope_provenance={}, content_hash="1007"))
    session.add(ProductSignal(tenant_id=t.id, sku="1007", store_view_magento_id=1,
                              uses_msi=False, is_in_stock=is_in_stock))
    if muestra is not None:
        session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                 key="show_out_of_stock", value="true" if muestra else "false"))
    session.flush()
    return t


def test_oculto_sin_stock_no_genera_hallazgos(db_session):
    t = _setup(db_session, is_in_stock=False, muestra=False)
    run = detect_store_view(db_session, t.id, 1)
    n = db_session.query(Finding).filter(Finding.run_id == run.id,
                                         Finding.subject_key == "1007").count()
    assert n == 0


def test_sin_stock_visible_si_genera_hallazgos(db_session):
    t = _setup(db_session, is_in_stock=False, muestra=True)
    run = detect_store_view(db_session, t.id, 1)
    n = db_session.query(Finding).filter(Finding.run_id == run.id,
                                         Finding.subject_key == "1007").count()
    assert n > 0


def test_sin_datos_de_stock_se_comporta_como_hoy(db_session):
    # is_in_stock None y sin config: el producto se evalúa como siempre.
    t = _setup(db_session, is_in_stock=None, muestra=None)
    run = detect_store_view(db_session, t.id, 1)
    n = db_session.query(Finding).filter(Finding.run_id == run.id,
                                         Finding.subject_key == "1007").count()
    assert n > 0
