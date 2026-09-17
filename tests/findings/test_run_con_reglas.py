from skudo.findings.models import Finding
from skudo.findings.run import detect_store_view
from skudo.mirror.models import Attribute, ProductRecord, Tenant
from skudo.rules.models import Rule, RulesetSnapshot


def _setup(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t); session.flush()
    session.add(Attribute(tenant_id=t.id, code="color", label="color",
                          frontend_input="select", declared_scope="global",
                          is_filterable=True, is_required=False, attribute_set_ids=[4]))
    # dos productos publicados del set 4; uno sin color
    for sku, attrs in [("A", {"status": "1", "visibility": "4", "color": "rojo"}),
                       ("B", {"status": "1", "visibility": "4"})]:
        session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                  attributes=attrs, attribute_set_id=4, type_id="simple",
                                  sync_generation=1, scope_provenance={}, content_hash=sku))
    session.flush()
    return t


def _regla_aceptada(session, t, **kw):
    r = Rule(tenant_id=t.id, scope_kind="attribute_set", scope_key="4",
             store_view_magento_id=1, axis=3, kind="obligatoriedad",
             definition={"attribute": "color", "marcaria": 1}, confidence=0.97,
             evidence_count=1, exceptions=[], status="aceptada", origin="inferida")
    for k, v in kw.items():
        setattr(r, k, v)
    session.add(r); session.flush()
    return r


def test_una_regla_aceptada_en_el_snapshot_produce_hallazgo_atribuido(db_session):
    t = _setup(db_session)
    r = _regla_aceptada(db_session, t)
    snap = RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                           rule_ids=[r.id])
    db_session.add(snap); db_session.flush()

    run = detect_store_view(db_session, t.id, 1)
    reglas = db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "regla:obligatoriedad:color").all()
    assert len(reglas) == 1
    assert reglas[0].subject_key == "B"
    assert reglas[0].rule_id == r.id
    assert reglas[0].ruleset_version == 1
    assert run.ruleset_version == 1


def test_una_regla_borrador_no_esta_en_el_snapshot_y_no_marca(db_session):
    t = _setup(db_session)
    _regla_aceptada(db_session, t, status="borrador")
    # sin snapshot creado: el motor de reglas no aporta, detectores especiales sí
    run = detect_store_view(db_session, t.id, 1)
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code.like("regla:%")).count() == 0
    assert run.ruleset_version is None


def test_los_detectores_especiales_siguen_corriendo(db_session):
    t = _setup(db_session)
    run = detect_store_view(db_session, t.id, 1)
    # sin_categoria es un detector especial: B (y A) no tienen categoría
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "sin_categoria").count() >= 1
