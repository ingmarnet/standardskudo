from skudo_testing import escribir, preparar
from sqlalchemy import select

from skudo.findings.models import DetectorCoverage, Finding, FindingRun
from skudo.findings.run import detect_store_view, findings_report
from skudo.rules.models import Rule


def sembrar(session, tenant):
    # Un publicado sin imagen, una variante sin imagen (que NO es defecto),
    # un configurable sin precio (que tampoco lo es) y tres talles sueltos.
    escribir(session, tenant, 1, "pub-sin-foto",
             {"name": "Paleta Nox", "status": "1", "visibility": "4"})
    escribir(session, tenant, 1, "variante",
             {"name": "Paleta Nox T2", "status": "1", "visibility": "1"})
    escribir(session, tenant, 1, "config",
             {"name": "Zapatilla Padre", "status": "1", "visibility": "4",
              "image": "/a.jpg"})
    for i in range(3):
        escribir(session, tenant, 1, f"talle{i}",
                 {"name": "CALZADO ASICS GEL CHALLENGER", "status": "1",
                  "visibility": "4", "image": "/b.jpg", "price": "500000"})
    session.execute(
        __import__("sqlalchemy").text(
            "update product_record set type_id='configurable' where sku='config'"
        )
    )


def test_la_pasada_guarda_hallazgos_y_cobertura(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)

    run = detect_store_view(db_session, tenant.id, 1)

    codigos = {
        c for (c,) in db_session.execute(
            select(Finding.code).where(Finding.run_id == run.id).distinct()
        )
    }
    assert "sin_imagen" in codigos
    assert "variantes_sueltas" in codigos

    coberturas = db_session.scalars(
        select(DetectorCoverage).where(DetectorCoverage.run_id == run.id)
    ).all()
    assert len(coberturas) == 20, "los veinte detectores declaran cobertura, siempre"
    assert all(c.evaluados + c.no_aplica + c.no_evaluado == run.product_count
               for c in coberturas)


def test_el_configurable_no_aparece_como_sin_precio(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = detect_store_view(db_session, tenant.id, 1)
    sin_precio = [
        f.subject_key for f in db_session.scalars(
            select(Finding).where(Finding.run_id == run.id, Finding.code == "sin_precio")
        )
    ]
    assert "config" not in sin_precio


def test_el_informe_lleva_el_denominador_pegado_al_numero(db_session):
    """Un hallazgo sin su cobertura al lado es un porcentaje esperando el
    denominador equivocado."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    informe = findings_report(db_session, detect_store_view(db_session, tenant.id, 1))

    assert informe["productos"] == 6
    for h in informe["por_deteccion"]:
        assert h["evaluados"] is not None, h["code"]
        assert h["porcentaje"] is not None, h["code"]
    assert informe["exclusiones"], "las exclusiones viajan en el informe"


def test_una_variante_no_navegable_no_genera_hallazgo_de_imagen(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = detect_store_view(db_session, tenant.id, 1)
    sin_img = {
        f.subject_key for f in db_session.scalars(
            select(Finding).where(Finding.run_id == run.id, Finding.code == "sin_imagen")
        )
    }
    assert sin_img == {"pub-sin-foto"}


def _regla(session, tenant, scope_key, attribute="color", **kw):
    r = Rule(
        tenant_id=tenant.id, scope_kind="attribute_set", scope_key=scope_key,
        store_view_magento_id=1, axis=3, kind="obligatoriedad",
        definition={"attribute": attribute}, confidence=0.9, evidence_count=10,
        exceptions=[], status="aceptada", origin="inferida",
    )
    for k, v in kw.items():
        setattr(r, k, v)
    session.add(r)
    session.flush()
    return r


def test_el_informe_deja_rule_id_ambiguo_si_dos_reglas_comparten_codigo(db_session):
    """`regla:obligatoriedad:color` en el attribute_set 4 y en el 9 son DOS
    reglas distintas que comparten kind+attribute (y por lo tanto código): el
    conteo agregado las suma, pero `rule_id` no puede atribuirse a una sola
    sin mentir, así que la fila debe quedar con `rule_id = None`."""
    tenant = preparar(db_session)
    r1 = _regla(db_session, tenant, "4")
    r2 = _regla(db_session, tenant, "9")
    run = FindingRun(
        tenant_id=tenant.id, store_view_magento_id=1, mirror_sync_generation=1,
        product_count=4,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add_all([
        Finding(run_id=run.id, code="regla:obligatoriedad:color", axis=3,
                severity="media", subject_type="producto", subject_key="A",
                evidence={}, rule_id=r1.id, ruleset_version=1),
        Finding(run_id=run.id, code="regla:obligatoriedad:color", axis=3,
                severity="media", subject_type="producto", subject_key="B",
                evidence={}, rule_id=r2.id, ruleset_version=1),
    ])
    db_session.flush()

    informe = findings_report(db_session, run)
    fila = next(
        h for h in informe["por_deteccion"] if h["code"] == "regla:obligatoriedad:color"
    )
    assert fila["hallazgos"] == 2
    assert fila["origen"] == "regla"
    assert fila["rule_id"] is None


def test_el_informe_atribuye_rule_id_cuando_una_sola_regla_produce_el_codigo(db_session):
    tenant = preparar(db_session)
    r = _regla(db_session, tenant, "4", attribute="talle")
    run = FindingRun(
        tenant_id=tenant.id, store_view_magento_id=1, mirror_sync_generation=1,
        product_count=2,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        Finding(run_id=run.id, code="regla:obligatoriedad:talle", axis=3,
                severity="media", subject_type="producto", subject_key="C",
                evidence={}, rule_id=r.id, ruleset_version=1)
    )
    db_session.flush()

    informe = findings_report(db_session, run)
    fila = next(
        h for h in informe["por_deteccion"] if h["code"] == "regla:obligatoriedad:talle"
    )
    assert fila["origen"] == "regla"
    assert fila["rule_id"] == r.id
