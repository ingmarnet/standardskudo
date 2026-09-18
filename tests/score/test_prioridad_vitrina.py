from skudo.findings.run import detect_store_view
from skudo.mirror.models import ProductRecord, ProductSignal, StoreSetting, Tenant
from skudo.score.models import ProductScore
from skudo.score.run import score_run


def _prod(session, t, sku, is_in_stock):
    session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                              attributes={"status": "1", "visibility": "4", "color": "rojo"},
                              attribute_set_id=4, type_id="simple",
                              sync_generation=1, scope_provenance={}, content_hash=sku))
    session.add(ProductSignal(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                              uses_msi=False, is_in_stock=is_in_stock))


def test_prioridad_se_persiste_y_oculto_no_se_puntua(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                key="show_out_of_stock", value="false"))  # oculta sin stock
    _prod(db_session, t, "con", is_in_stock=True)     # pleno
    _prod(db_session, t, "vis", is_in_stock=None)     # desconocido: se sigue
                                                       # puntuando (ignorancia
                                                       # no oculta), pero no se
                                                       # declara "pleno"
    _prod(db_session, t, "ocu", is_in_stock=False)    # oculto: no debe puntuarse
    db_session.flush()

    run = detect_store_view(db_session, t.id, 1)
    score_run(db_session, run)

    por_sku = {p.sku: p for p in db_session.query(ProductScore).filter_by(
        tenant_id=t.id, store_view_magento_id=1)}
    assert por_sku["con"].prioridad_vitrina == "pleno"
    # is_in_stock=None sigue puntuado (la ignorancia no oculta), pero la
    # prioridad de vitrina no puede afirmar "pleno" sin datos de stock
    # (spec §6): se declara "desconocido", no se confunde con un veredicto.
    assert por_sku["vis"].prioridad_vitrina == "desconocido"
    assert "ocu" not in por_sku  # oculto por falta de stock: no se puntúa
