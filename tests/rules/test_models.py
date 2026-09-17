import pytest
from sqlalchemy.exc import IntegrityError

from skudo.mirror.models import Tenant
from skudo.rules.models import (
    ConceptMap,
    GoogleFloor,
    Rule,
    RulesetSnapshot,
    RuleVersion,
)


def _tenant(session) -> Tenant:
    t = Tenant(code="acme", name="Acme", base_url="http://acme.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def test_una_regla_nace_borrador_con_su_evidencia(db_session):
    t = _tenant(db_session)
    r = Rule(
        tenant_id=t.id,
        scope_kind="attribute_set",
        scope_key="4",
        store_view_magento_id=1,
        axis=3,
        kind="obligatoriedad",
        definition={"attribute": "color"},
        confidence=0.97,
        evidence_count=120,
        exceptions=[],
        status="borrador",
        origin="inferida",
    )
    db_session.add(r)
    db_session.flush()
    assert r.status == "borrador"
    assert r.false_positive_rate is None, "el FP nace nulo, lo mide S1c"
    assert r.ruleset_version is None


def test_google_floor_es_de_referencia_no_por_tenant(db_session):
    # No tiene tenant_id: los requisitos de Google son iguales para todos.
    f = GoogleFloor(
        category_group="Apparel & Accessories",
        google_category_min=166,
        google_category_max=8000,
        google_attribute="color",
        requirement="required",
        axis=3,
        applicability={},
        note="indumentaria exige color",
    )
    db_session.add(f)
    db_session.flush()
    assert f.id is not None


def test_el_concepto_admite_varios_atributos_por_canonico(db_session):
    t = _tenant(db_session)
    db_session.add(ConceptMap(tenant_id=t.id, canonical="color", attribute_code="color",
                              relation="equivalente", confidence=1.0, origin="curada"))
    db_session.add(ConceptMap(tenant_id=t.id, canonical="color", attribute_code="colour",
                              relation="sinonimo", confidence=0.8, origin="inferida"))
    db_session.flush()  # dos filas del mismo canónico conviven

    with pytest.raises(IntegrityError):
        # la misma (tenant, canonical, attribute_code) no se repite
        db_session.add(ConceptMap(tenant_id=t.id, canonical="color",
                                  attribute_code="color", relation="equivalente",
                                  confidence=1.0, origin="curada"))
        db_session.flush()


def test_una_transicion_guarda_el_snapshot_de_la_definicion(db_session):
    t = _tenant(db_session)
    r = Rule(tenant_id=t.id, scope_kind="global", scope_key="*", axis=3,
             kind="obligatoriedad", definition={"attribute": "x"}, confidence=1.0,
             evidence_count=1, exceptions=[], status="borrador", origin="curada")
    db_session.add(r)
    db_session.flush()
    v = RuleVersion(rule_id=r.id, from_status=None, to_status="borrador",
                    actor="inferencia", motivo="alta",
                    definition_snapshot={"attribute": "x"})
    db_session.add(v)
    db_session.flush()
    assert v.created_at is not None


def test_un_snapshot_congela_ids_de_regla(db_session):
    t = _tenant(db_session)
    s = RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                        rule_ids=[1, 2, 3])
    db_session.add(s)
    db_session.flush()
    assert s.rule_ids == [1, 2, 3]
