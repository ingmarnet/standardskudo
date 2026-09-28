"""S2 — evidencia por dato: trazabilidad completa y conflictos declarados.

Spec §6.6: cada dato inferido o corregido guarda fuente, fragmento o fila
exacta, fecha, identidad del producto y transformación aplicada. Cuando dos
fuentes discrepan, el conflicto se guarda como conflicto, no se resuelve solo.
"""

from skudo.evidence.models import Evidence
from skudo.evidence.service import conflicts_for_subject, list_for_subject, record
from skudo.mirror.models import Tenant


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def test_record_guarda_trazabilidad_completa(db_session):
    t = _tenant(db_session)
    ev = record(
        db_session,
        tenant_id=t.id,
        subject_type="rule",
        subject_id=1,
        datum="cobertura",
        source="perfil",
        fragment="partición attribute_set:4 atributo=color presente=194 vacio=6",
        value={"coverage": 0.97, "presente": 194, "vacio": 6},
    )
    got = db_session.get(Evidence, ev.id)
    assert got.source == "perfil"
    assert "atributo=color" in got.fragment
    assert got.value["coverage"] == 0.97
    assert got.product_sku is None
    assert got.transformation is None
    assert got.conflict_state == "sin_conflicto"
    assert got.observed_at is not None, "la fecha de observación queda grabada"


def test_conflicto_entre_dos_fuentes_se_guarda_como_conflicto_sin_resolver(db_session):
    t = _tenant(db_session)
    a = record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=7, datum="rango",
        source="perfil", fragment="p05..p95 sobre 200 productos",
        value={"min": 0.5, "max": 8.0},
    )
    b = record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=7, datum="rango",
        source="canal", fragment="ficha del fabricante",
        value={"min": 0.5, "max": 12.0},
    )
    db_session.flush()
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.conflict_state == "en_conflicto"
    assert b.conflict_state == "en_conflicto"
    assert a.conflict_group == b.conflict_group, "las fuentes en desacuerdo comparten grupo"
    # no se resuelve solo: ambas sobreviven con sus valores
    assert a.value["max"] == 8.0
    assert b.value["max"] == 12.0


def test_dos_fuentes_que_coinciden_no_son_conflicto(db_session):
    t = _tenant(db_session)
    a = record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=9, datum="cobertura",
        source="perfil", fragment="x", value={"coverage": 0.9},
    )
    b = record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=9, datum="cobertura",
        source="canal", fragment="y", value={"coverage": 0.9},
    )
    db_session.flush()
    db_session.refresh(a)
    db_session.refresh(b)
    assert a.conflict_state == "sin_conflicto"
    assert b.conflict_state == "sin_conflicto"


def test_conflicts_for_subject_agrupa_los_desacuerdos(db_session):
    t = _tenant(db_session)
    record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=3, datum="cobertura",
        source="perfil", fragment="x", value={"coverage": 0.97},
    )
    record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=3, datum="cobertura",
        source="canal", fragment="y", value={"coverage": 0.85},
    )
    db_session.flush()
    grupos = conflicts_for_subject(db_session, "rule", 3)
    assert len(grupos) == 1
    assert len(grupos[0]) == 2


def test_list_for_subject_solo_devuelve_las_de_ese_subject(db_session):
    t = _tenant(db_session)
    record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=1, datum="cobertura",
        source="perfil", fragment="x", value={"coverage": 0.9},
    )
    record(
        db_session, tenant_id=t.id, subject_type="rule", subject_id=2, datum="cobertura",
        source="perfil", fragment="x", value={"coverage": 0.8},
    )
    db_session.flush()
    assert len(list_for_subject(db_session, "rule", 1)) == 1
