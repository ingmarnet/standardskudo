"""S2 — la inferencia adjunta evidencia a cada regla (la propuesta muestra de dónde salió)."""

from datetime import UTC, datetime

from skudo.evidence.service import list_for_subject
from skudo.mirror.models import Tenant
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun, ValueStats
from skudo.rules.inference import inferir


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _run_con_particion(session, tenant):
    run = ProfileRun(
        tenant_id=tenant.id, store_view_magento_id=1, mirror_sync_generation=1,
        thresholds={}, product_count=200, finished_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    part = ProfilePartition(
        run_id=run.id, attribute_set_id=4, splitter_kind="ninguno", splitter_key=None,
        splitter_value=None, product_count=200, ambiguity=0.1,
        decision_reason="sin candidatos",
    )
    session.add(part)
    session.flush()
    return run, part


def test_cada_regla_inferida_lleva_evidencia_de_su_origen(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    db_session.add(AttributeCoverage(
        partition_id=part.id, attribute_code="color", presente=194, vacio=6,
        no_aplica=0, desconocido=0, coverage=0.97,
    ))
    db_session.add(ValueStats(
        partition_id=part.id, attribute_code="peso", kind="numerico", n_present=200,
        n_ambiguous=2, minimum=0.1, p05=0.5, p50=2.0, p95=8.0, maximum=50.0,
        distinct_values=40, mode_share=0.1, top_values=[],
    ))
    db_session.flush()

    reglas = inferir(db_session, run.id)

    assert len(reglas) >= 2  # obligatoriedad:color + rango:peso
    for r in reglas:
        ev = list_for_subject(db_session, "rule", r.id)
        assert ev, f"la regla {r.kind}:{r.definition.get('attribute')} no lleva evidencia"
        assert ev[0].source == "perfil"
        assert "perfil" in ev[0].fragment
        assert ev[0].tenant_id == t.id
        assert ev[0].conflict_state == "sin_conflicto"
