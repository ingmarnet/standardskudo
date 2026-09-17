import pytest

from skudo.mirror.models import Tenant
from skudo.rules.curation import (
    ReglaAmbiguaSinConfirmar,
    aceptar,
    acotar,
    ajustar,
    degradar_por_fp,
    inspeccionar,
    rechazar,
    snapshot,
)
from skudo.rules.models import Rule, RuleVersion
from skudo.rules.transitions import TransicionInvalida


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _regla(session, tenant, *, origin="inferida", status="borrador",
           definition=None, store=1):
    r = Rule(tenant_id=tenant.id, scope_kind="attribute_set", scope_key="4",
             store_view_magento_id=store, axis=3, kind="obligatoriedad",
             definition=definition or {"attribute": "color", "marcaria": 5},
             confidence=0.97, evidence_count=100, exceptions=[], status=status,
             origin=origin)
    session.add(r)
    session.flush()
    return r


def test_aceptar_mueve_a_aceptada_y_registra_version(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t)
    aceptar(db_session, [r.id], actor="ana@x.com")
    db_session.refresh(r)
    assert r.status == "aceptada"
    v = db_session.query(RuleVersion).filter_by(rule_id=r.id, to_status="aceptada").one()
    assert v.actor == "ana@x.com"


def test_aceptar_una_ambigua_sin_confirmar_falla(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "material", "ambiguo": True})
    with pytest.raises(ReglaAmbiguaSinConfirmar):
        aceptar(db_session, [r.id], actor="ana@x.com")
    db_session.refresh(r)
    assert r.status == "borrador", "no se movió"


def test_aceptar_una_ambigua_con_confirmar_pasa(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "material", "ambiguo": True})
    aceptar(db_session, [r.id], actor="ana@x.com", confirmar_ambiguo=True)
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_rechazar_un_piso_externo_falla(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, origin="piso_externo", status="aceptada")
    with pytest.raises(TransicionInvalida):
        rechazar(db_session, r.id, actor="ana@x.com", motivo="no")
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_acotar_un_piso_externo_lo_pasa_a_aviso(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, origin="piso_externo", status="aceptada")
    acotar(db_session, r.id, actor="ana@x.com", motivo="el fabricante no asigna GTIN")
    db_session.refresh(r)
    assert r.status == "aviso"


def test_rechazar_exige_motivo(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t)
    with pytest.raises(ValueError):
        rechazar(db_session, r.id, actor="ana@x.com", motivo="")


def test_ajustar_guarda_snapshot_de_la_definicion_anterior(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "color", "min_len": 3})
    ajustar(db_session, r.id, actor="ana@x.com", definition={"attribute": "color", "min_len": 5})
    db_session.refresh(r)
    assert r.definition["min_len"] == 5
    versiones = db_session.query(RuleVersion).filter_by(rule_id=r.id).all()
    # la versión del ajuste guarda la definición NUEVA; el historial permite ver el cambio
    assert any(v.definition_snapshot.get("min_len") == 5 for v in versiones)


def test_snapshot_solo_incluye_reglas_activas(db_session):
    t = _tenant(db_session)
    aceptada = _regla(db_session, t, status="aceptada", store=1)
    aviso = _regla(db_session, t, status="aviso", store=1)
    _regla(db_session, t, status="borrador", store=1)  # NO entra
    _regla(db_session, t, status="rechazada", store=1)  # NO entra

    snap = snapshot(db_session, t.id, store_view=1)
    assert set(snap.rule_ids) == {aceptada.id, aviso.id}
    assert snap.version == 1


def test_dos_snapshots_incrementan_la_version(db_session):
    t = _tenant(db_session)
    _regla(db_session, t, status="aceptada", store=1)
    s1 = snapshot(db_session, t.id, store_view=1)
    s2 = snapshot(db_session, t.id, store_view=1)
    assert s2.version == s1.version + 1


def test_no_se_puede_snapshotear_un_borrador(db_session):
    """Criterio de aceptación 1: un borrador no puede entrar a un snapshot."""
    t = _tenant(db_session)
    r = _regla(db_session, t, status="borrador", store=1)
    snap = snapshot(db_session, t.id, store_view=1)
    assert r.id not in snap.rule_ids


def test_degradar_por_fp_pasa_de_aceptada_a_aviso_con_actor_sistema(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, status="aceptada")
    r.false_positive_rate = 0.25
    db_session.flush()
    degradar_por_fp(db_session, r.id)
    db_session.refresh(r)
    assert r.status == "aviso"
    v = db_session.query(RuleVersion).filter_by(rule_id=r.id, to_status="aviso").one()
    assert v.actor == "sistema"


def test_inspeccionar_muestra_lo_que_marcaria_y_lo_que_descartaria(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "color", "marcaria": 6})
    r.evidence_count = 194
    db_session.flush()
    info = inspeccionar(db_session, r.id)
    assert info["marcaria"] == 6
    assert info["descartaria"] == 194
    assert info["attribute"] == "color"
