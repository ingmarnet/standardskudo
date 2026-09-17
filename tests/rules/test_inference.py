from skudo.mirror.models import Tenant
from skudo.profile.models import (
    AttributeCoverage,
    ProfilePartition,
    ProfileRun,
    ValueStats,
)
from skudo.rules.inference import (
    MIN_EVIDENCIA,
    UMBRAL_ALTO,
    UMBRAL_MEDIO,
    inferir,
)


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _run_con_particion(session, tenant, *, splitter_value=None):
    run = ProfileRun(tenant_id=tenant.id, store_view_magento_id=1,
                     mirror_sync_generation=1, thresholds={}, product_count=200)
    session.add(run)
    session.flush()
    part = ProfilePartition(
        run_id=run.id, attribute_set_id=4,
        splitter_kind="ninguno" if splitter_value is None else "atributo",
        splitter_key=None if splitter_value is None else "tipo",
        splitter_value=splitter_value, product_count=200, ambiguity=0.1,
        decision_reason="elegido" if splitter_value else "sin candidatos",
    )
    session.add(part)
    session.flush()
    return run, part


def _cobertura(session, part, code, *, presente, vacio):
    total = presente + vacio
    session.add(AttributeCoverage(
        partition_id=part.id, attribute_code=code, presente=presente, vacio=vacio,
        no_aplica=0, desconocido=0,
        coverage=(presente / total) if total else None,
    ))
    session.flush()


def test_cobertura_alta_infiere_obligatoriedad_con_confianza_igual_a_cobertura(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "color", presente=194, vacio=6)  # 0.97

    reglas = inferir(db_session, run.id)

    obl = [r for r in reglas if r.kind == "obligatoriedad" and r.definition["attribute"] == "color"]
    assert len(obl) == 1
    r = obl[0]
    assert abs(r.confidence - 0.97) < 1e-9, "la confianza ES la cobertura"
    assert r.evidence_count == 194
    assert r.status == "borrador"
    assert r.origin == "inferida"
    assert r.store_view_magento_id == 1, "nace con store view, nunca NULL"
    assert r.profile_run_id == run.id
    assert "ambiguo" not in r.definition, "banda alta no marca ambiguo"


def test_cobertura_media_marca_ambiguo(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "material", presente=160, vacio=40)  # 0.80

    reglas = inferir(db_session, run.id)
    r = next(r for r in reglas if r.definition["attribute"] == "material")
    assert r.definition.get("ambiguo") is True
    assert UMBRAL_MEDIO <= r.confidence < UMBRAL_ALTO


def test_cobertura_baja_no_infiere(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "regalo", presente=100, vacio=100)  # 0.50

    reglas = inferir(db_session, run.id)
    assert not any(r.definition.get("attribute") == "regalo" for r in reglas)


def test_muestra_chica_no_infiere_aunque_la_cobertura_sea_perfecta(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "raro", presente=MIN_EVIDENCIA - 1, vacio=0)  # 1.0

    reglas = inferir(db_session, run.id)
    assert not any(r.definition.get("attribute") == "raro" for r in reglas)


def test_las_excepciones_son_los_productos_sin_el_atributo(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "color", presente=194, vacio=6)
    reglas = inferir(db_session, run.id)
    r = next(r for r in reglas if r.definition["attribute"] == "color")
    # No tenemos SKUs en la cobertura agregada: la excepción se declara como
    # conteo y la lista de SKUs la completa el detector de S1c. La regla guarda
    # cuántos marcaría.
    assert r.exceptions == [] or isinstance(r.exceptions, list)
    assert r.definition["marcaria"] == 6, "lo que la regla marcaría: los vacíos"


def test_una_particion_con_subtipo_produce_scope_subtype(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t, splitter_value="ropa")
    _cobertura(db_session, part, "talle", presente=190, vacio=10)
    reglas = inferir(db_session, run.id)
    r = next(r for r in reglas if r.definition["attribute"] == "talle")
    assert r.scope_kind == "subtype"
    assert r.scope_key == "ropa"


def test_rango_numerico_usa_p05_p95(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    db_session.add(ValueStats(
        partition_id=part.id, attribute_code="peso", kind="numerico",
        n_present=200, n_ambiguous=2, minimum=0.1, p05=0.5, p50=2.0, p95=8.0,
        maximum=50.0, distinct_values=40, mode_share=0.1, top_values=[],
    ))
    db_session.flush()
    reglas = inferir(db_session, run.id)
    rango = next(r for r in reglas if r.kind == "rango")
    assert rango.definition["min"] == 0.5
    assert rango.definition["max"] == 8.0
    assert rango.confidence > 0.98  # 1 - 2/200


def test_numerico_sucio_no_da_rango_sino_sospecha_de_conversion(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    db_session.add(ValueStats(
        partition_id=part.id, attribute_code="voltaje", kind="numerico",
        n_present=200, n_ambiguous=40, minimum=0, p05=110, p50=220, p95=380,
        maximum=999, distinct_values=5, mode_share=0.6, top_values=[],
    ))  # 40/200 = 0.20 > MAX_RATIO_AMBIGUO
    db_session.flush()
    reglas = inferir(db_session, run.id)
    assert not any(r.kind == "rango" and r.definition.get("attribute") == "voltaje"
                   for r in reglas)
    fmt = next(r for r in reglas if r.kind == "formato"
               and r.definition.get("attribute") == "voltaje")
    assert fmt.definition["sospecha_conversion"] is True


def test_reproducible_dos_inferencias_dan_lo_mismo(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "color", presente=194, vacio=6)
    _cobertura(db_session, part, "marca", presente=180, vacio=20)

    def firma(reglas):
        return sorted((r.kind, r.definition["attribute"], round(r.confidence, 6))
                      for r in reglas)

    r1 = firma(inferir(db_session, run.id))
    # limpiar y re-inferir sobre el mismo perfil
    from skudo.rules.models import Rule, RuleVersion
    # rule_version no tiene ON DELETE CASCADE (migración 0019 de Task 1): se
    # borra primero el historial y después la regla.
    for rv in db_session.query(RuleVersion).all():
        db_session.delete(rv)
    for r in db_session.query(Rule).all():
        db_session.delete(r)
    db_session.flush()
    r2 = firma(inferir(db_session, run.id))
    assert r1 == r2
