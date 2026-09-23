"""El arnés de FP: etiquetar hallazgos y medir la tasa por regla y detector.

La tasa sale de etiquetas humanas, nunca de una intuición. `no_aplica` no
cuenta en la tasa: aparta un hallazgo que no correspondía emitir, no es ni un
acierto ni un fallo.
"""

from skudo_testing import preparar

from skudo.findings.labels import etiquetar, medir_fp
from skudo.findings.models import Finding, FindingRun
from skudo.rules.models import Rule


def _regla_y_findings(session, tenant, n=10):
    r = Rule(
        tenant_id=tenant.id, scope_kind="attribute_set", scope_key="4",
        store_view_magento_id=1, axis=3, kind="obligatoriedad",
        definition={"attribute": "color"}, confidence=0.9, evidence_count=10,
        exceptions=[], status="aceptada", origin="inferida",
    )
    session.add(r)
    session.flush()
    run = FindingRun(
        tenant_id=tenant.id, store_view_magento_id=1, mirror_sync_generation=1,
        product_count=n,
    )
    session.add(run)
    session.flush()
    findings = [
        Finding(run_id=run.id, code="regla:obligatoriedad:color", axis=3,
                severity="media", subject_type="producto", subject_key=f"A{i}",
                evidence={}, rule_id=r.id, ruleset_version=1)
        for i in range(n)
    ]
    session.add_all(findings)
    session.flush()
    return r, findings


def test_la_tasa_sale_de_las_etiquetas_y_degrada_la_regla(db_session):
    tenant = preparar(db_session)
    r, findings = _regla_y_findings(db_session, tenant)
    for i, f in enumerate(findings):
        etiquetar(db_session, f.id, "verdadero" if i < 2 else "falso", "curador@x")
    res = medir_fp(db_session, tenant.id)
    assert r.false_positive_rate == 0.8
    assert r.status == "aviso", "0.8 de FP degrada sola una regla aceptada"
    assert res["reglas"][str(r.id)]["fp"] == 0.8


def test_no_aplica_no_cuenta_en_la_tasa(db_session):
    tenant = preparar(db_session)
    r, findings = _regla_y_findings(db_session, tenant, n=3)
    etiquetar(db_session, findings[0].id, "verdadero", "c")
    etiquetar(db_session, findings[1].id, "falso", "c")
    etiquetar(db_session, findings[2].id, "no_aplica", "c")
    medir_fp(db_session, tenant.id)
    assert r.false_positive_rate == 0.5  # 1 falso sobre 2 que cuentan


def test_reetiquetar_sobrescribe_no_duplica(db_session):
    tenant = preparar(db_session)
    r, findings = _regla_y_findings(db_session, tenant, n=1)
    etiquetar(db_session, findings[0].id, "falso", "c")
    etiquetar(db_session, findings[0].id, "verdadero", "c")
    medir_fp(db_session, tenant.id)
    assert r.false_positive_rate == 0.0  # la última decisión manda


def test_un_detector_se_mide_sin_regla(db_session):
    tenant = preparar(db_session)
    run = FindingRun(tenant_id=tenant.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=2)
    db_session.add(run)
    db_session.flush()
    f = Finding(run_id=run.id, code="sin_imagen", axis=1, severity="media",
                subject_type="producto", subject_key="X", evidence={})
    db_session.add(f)
    db_session.flush()
    etiquetar(db_session, f.id, "falso", "c")
    res = medir_fp(db_session, tenant.id)
    assert res["detectores"]["sin_imagen"]["fp"] == 1.0
    assert res["reglas"] == {}
