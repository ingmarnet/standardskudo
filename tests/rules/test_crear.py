"""Creación manual de reglas (origin `curada`).

Una persona declara la regla (atributo, alcance, tipo, rango); nace en borrador
y entra al mismo circuito de curación que las inferidas. `confidence = 1.0` es
una decisión humana, no una medición (precedente: el seed curado del mapa de
conceptos), así que NO viola §5 —que prohíbe escribir a mano la confianza de una
regla INFERIDA—.
"""

import pytest

from skudo.mirror.models import Tenant
from skudo.rules import curation
from skudo.rules.curation import ReglaDuplicada
from skudo.rules.models import Rule, RuleVersion


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def test_crear_obligatoriedad_nace_curada_en_borrador(db_session):
    t = _tenant(db_session)
    r = curation.crear(
        db_session, tenant_id=t.id, store_view_magento_id=1,
        scope_kind="attribute_set", scope_key="4", kind="obligatoriedad",
        attribute="color", actor="ana@x.com",
    )
    assert r.origin == "curada"
    assert r.status == "borrador"
    assert r.confidence == 1.0
    assert r.profile_run_id is None
    assert r.definition == {"attribute": "color"}
    # deja rastro en el historial
    ver = db_session.query(RuleVersion).filter_by(rule_id=r.id).one()
    assert ver.from_status is None and ver.to_status == "borrador"
    assert ver.actor == "ana@x.com"


def test_crear_rango_guarda_min_y_max(db_session):
    t = _tenant(db_session)
    r = curation.crear(
        db_session, tenant_id=t.id, store_view_magento_id=1,
        scope_kind="global", scope_key=None, kind="rango",
        attribute="peso", actor="ana@x.com", minimo=0.1, maximo=5.0,
    )
    assert r.scope_kind == "global"
    assert r.scope_key == "*"
    assert r.definition == {"attribute": "peso", "min": 0.1, "max": 5.0}


def test_crear_rechaza_atributo_vacio(db_session):
    t = _tenant(db_session)
    with pytest.raises(ValueError):
        curation.crear(
            db_session, tenant_id=t.id, store_view_magento_id=1,
            scope_kind="global", scope_key=None, kind="obligatoriedad",
            attribute="   ", actor="ana@x.com",
        )


def test_crear_rango_exige_min_menor_que_max(db_session):
    t = _tenant(db_session)
    with pytest.raises(ValueError):
        curation.crear(
            db_session, tenant_id=t.id, store_view_magento_id=1,
            scope_kind="global", scope_key=None, kind="rango",
            attribute="peso", actor="ana@x.com", minimo=5.0, maximo=5.0,
        )


def test_crear_rechaza_duplicado_exacto(db_session):
    t = _tenant(db_session)
    curation.crear(
        db_session, tenant_id=t.id, store_view_magento_id=1,
        scope_kind="attribute_set", scope_key="4", kind="obligatoriedad",
        attribute="color", actor="ana@x.com",
    )
    with pytest.raises(ReglaDuplicada):
        curation.crear(
            db_session, tenant_id=t.id, store_view_magento_id=1,
            scope_kind="attribute_set", scope_key="4", kind="obligatoriedad",
            attribute="color", actor="otra@x.com",
        )


def test_crear_permite_misma_regla_en_otro_set(db_session):
    t = _tenant(db_session)
    curation.crear(
        db_session, tenant_id=t.id, store_view_magento_id=1,
        scope_kind="attribute_set", scope_key="4", kind="obligatoriedad",
        attribute="color", actor="ana@x.com",
    )
    # mismo atributo, distinto set: NO es duplicado
    r2 = curation.crear(
        db_session, tenant_id=t.id, store_view_magento_id=1,
        scope_kind="attribute_set", scope_key="9", kind="obligatoriedad",
        attribute="color", actor="ana@x.com",
    )
    assert r2.scope_key == "9"
    assert db_session.query(Rule).filter_by(tenant_id=t.id).count() == 2
