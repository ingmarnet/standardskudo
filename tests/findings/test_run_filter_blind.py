"""Filter-blind end-to-end por detect_store_view."""

from skudo.findings.models import Finding
from skudo.findings.run import detect_store_view
from skudo.mirror.models import Attribute, ProductRecord, Tenant


def test_atributo_filtrable_vacio_marca_por_la_pasada(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    # color filtrable, aplica al set 4
    db_session.add(Attribute(tenant_id=t.id, code="color", label="Color",
                             frontend_input="select", declared_scope="global",
                             is_filterable=True, is_required=False, attribute_set_ids=[4]))
    # marca no filtrable -> no debe producir filtro_ciego aunque esté vacía
    db_session.add(Attribute(tenant_id=t.id, code="marca", label="Marca",
                             frontend_input="text", declared_scope="global",
                             is_filterable=False, is_required=False, attribute_set_ids=[4]))
    for sku, attrs in [("A", {"status": "1", "visibility": "4", "color": "rojo"}),
                       ("B", {"status": "1", "visibility": "4"})]:  # B sin color
        db_session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                     attributes=attrs, attribute_set_id=4, type_id="simple",
                                     sync_generation=1, scope_provenance={}, content_hash=sku))
    db_session.flush()

    run = detect_store_view(db_session, t.id, 1)
    fc = db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "filtro_ciego:color").all()
    assert [f.subject_key for f in fc] == ["B"]
    # marca no filtrable: ningún hallazgo de filtro ciego
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "filtro_ciego:marca").count() == 0
