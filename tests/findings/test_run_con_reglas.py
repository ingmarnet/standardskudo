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


def test_una_regla_rechazada_tras_snapshotear_ya_no_penaliza(db_session):
    """El snapshot congela IDs, no estado. Si la regla se rechazó DESPUÉS de
    quedar en el snapshot, un `evaluate --ruleset <version vieja>` no debe
    resucitarla: el estado que manda es el vivo, no el de cuando se armó el
    snapshot."""
    t = _setup(db_session)
    r = _regla_aceptada(db_session, t)
    snap = RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                           rule_ids=[r.id])
    db_session.add(snap); db_session.flush()

    r.status = "rechazada"
    db_session.flush()

    run = detect_store_view(db_session, t.id, 1, ruleset_version=1)
    assert run.ruleset_version == 1
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "regla:obligatoriedad:color").count() == 0


def test_los_detectores_especiales_siguen_corriendo(db_session):
    t = _setup(db_session)
    run = detect_store_view(db_session, t.id, 1)
    # sin_categoria es un detector especial: B (y A) no tienen categoría
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "sin_categoria").count() >= 1


def test_dos_reglas_mismo_atributo_distinto_set_fusionan_cobertura(db_session):
    """Dos reglas de obligatoriedad sobre el mismo atributo en attribute_sets
    distintos producen el mismo código `regla:obligatoriedad:color`. La
    cobertura debe FUSIONARSE en una sola fila (evaluados sumados sobre scopes
    disjuntos), no chocar contra el único (run_id, detector) ni escribir dos
    filas. Bug detectado con datos reales de Renovapadel."""
    from skudo.findings.models import DetectorCoverage

    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(Attribute(tenant_id=t.id, code="color", label="color",
                             frontend_input="select", declared_scope="global",
                             is_filterable=True, is_required=False,
                             attribute_set_ids=[4, 9]))
    # set 4: A (con color) y B (sin) · set 9: C (con) y D (sin)
    for sku, sid, attrs in [
        ("A", 4, {"status": "1", "visibility": "4", "color": "rojo"}),
        ("B", 4, {"status": "1", "visibility": "4"}),
        ("C", 9, {"status": "1", "visibility": "4", "color": "azul"}),
        ("D", 9, {"status": "1", "visibility": "4"}),
    ]:
        db_session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                     attributes=attrs, attribute_set_id=sid,
                                     type_id="simple", sync_generation=1,
                                     scope_provenance={}, content_hash=sku))
    db_session.flush()

    r4 = _regla_aceptada(db_session, t, scope_key="4")
    r9 = _regla_aceptada(db_session, t, scope_key="9")
    snap = RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                           rule_ids=[r4.id, r9.id])
    db_session.add(snap); db_session.flush()

    run = detect_store_view(db_session, t.id, 1)

    # una sola fila de cobertura para el código, con evaluados fusionados (2 en
    # cada set = 4) y los totales cuadran con los 4 productos.
    cubs = db_session.query(DetectorCoverage).filter(
        DetectorCoverage.run_id == run.id,
        DetectorCoverage.detector == "regla:obligatoriedad:color").all()
    assert len(cubs) == 1
    c = cubs[0]
    assert c.evaluados == 4
    assert c.evaluados + c.no_aplica + c.no_evaluado == 4

    # los dos hallazgos (B y D), cada uno atribuido a su regla de scope.
    hs = db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "regla:obligatoriedad:color").all()
    assert {h.subject_key for h in hs} == {"B", "D"}
    assert {h.rule_id for h in hs} == {r4.id, r9.id}
