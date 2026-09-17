from skudo.findings.models import Finding, FindingRun
from skudo.mirror.models import Tenant


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t); session.flush()
    return t


def test_una_pasada_sella_su_version_de_ruleset(db_session):
    t = _tenant(db_session)
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=0, ruleset_version=7)
    db_session.add(run); db_session.flush()
    assert run.ruleset_version == 7


def test_un_hallazgo_de_regla_lleva_rule_id_y_version(db_session):
    t = _tenant(db_session)
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=0)
    db_session.add(run); db_session.flush()
    f = Finding(run_id=run.id, code="regla:obligatoriedad:color", axis=3,
                severity="media", subject_type="producto", subject_key="P1",
                evidence={"attribute": "color", "rule_id": 42},
                rule_id=None, ruleset_version=7)
    db_session.add(f); db_session.flush()
    assert f.ruleset_version == 7


def test_un_hallazgo_de_detector_deja_la_atribucion_nula(db_session):
    t = _tenant(db_session)
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=0)
    db_session.add(run); db_session.flush()
    f = Finding(run_id=run.id, code="sin_imagen", axis=7, severity="alta",
                subject_type="producto", subject_key="P1", evidence={})
    db_session.add(f); db_session.flush()
    assert f.rule_id is None and f.ruleset_version is None
