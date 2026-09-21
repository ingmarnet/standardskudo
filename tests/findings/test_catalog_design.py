"""Eje 11 — diseño del catálogo: sets muertos, filtro inútil, filtro perdido."""

from datetime import UTC, datetime

from skudo.findings.catalog_design import (
    filtros_inutiles,
    filtros_perdidos,
    sets_muertos,
)
from skudo.mirror.models import Attribute, AttributeSet, ProductRecord, Tenant
from skudo.profile.models import (
    AttributeCoverage,
    ProfilePartition,
    ProfileRun,
    ValueStats,
)


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _attr(session, t, code, *, filterable, sets):
    session.add(Attribute(
        tenant_id=t.id, code=code, label=code.title(), frontend_input="select",
        declared_scope="global", is_filterable=filterable, is_required=False,
        attribute_set_ids=sets))


def _prod(session, t, sku, sid):
    session.add(ProductRecord(
        tenant_id=t.id, sku=sku, store_view_magento_id=1, attributes={},
        attribute_set_id=sid, type_id="simple", sync_generation=1,
        scope_provenance={}, content_hash=sku))


def _perfil(session, t):
    run = ProfileRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, thresholds={}, product_count=100,
                     finished_at=datetime.now(UTC))
    session.add(run); session.flush()
    part = ProfilePartition(run_id=run.id, attribute_set_id=4, splitter_kind="set",
                            splitter_key=None, splitter_value=None, product_count=100,
                            decision_reason="elegido")
    session.add(part); session.flush()
    return run, part


# --- sets muertos ---------------------------------------------------------

def test_sets_muertos_son_los_definidos_sin_producto(db_session):
    t = _tenant(db_session)
    # el catálogo de atributos habla de los sets 4, 9 y 16
    _attr(db_session, t, "color", filterable=True, sets=[4, 9, 16])
    # pero sólo hay productos en 4 y 16 -> el 9 está muerto
    _prod(db_session, t, "A", 4)
    _prod(db_session, t, "B", 16)
    db_session.add(AttributeSet(tenant_id=t.id, magento_id=9, name="Set Viejo"))
    db_session.flush()

    muertos = sets_muertos(db_session, t.id)
    assert muertos == [{"magento_id": 9, "name": "Set Viejo"}]


def test_sets_muertos_sin_nombre_sincronizado(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "color", filterable=True, sets=[4, 7])
    _prod(db_session, t, "A", 4)
    db_session.flush()
    assert sets_muertos(db_session, t.id) == [{"magento_id": 7, "name": None}]


# --- filtro inútil --------------------------------------------------------

def test_filtro_inutil_un_solo_valor_domina(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "color", filterable=True, sets=[4])
    _attr(db_session, t, "marca", filterable=True, sets=[4])
    run, part = _perfil(db_session, t)
    # color: un valor domina el 98 % -> filtro inútil
    db_session.add(ValueStats(partition_id=part.id, attribute_code="color", kind="texto",
                              n_present=100, mode_share=0.98, distinct_values=3,
                              top_values=[["rojo", 98], ["azul", 1]]))
    # marca: reparte -> filtro útil, no se marca
    db_session.add(ValueStats(partition_id=part.id, attribute_code="marca", kind="texto",
                              n_present=100, mode_share=0.3, distinct_values=10,
                              top_values=[["nike", 30]]))
    db_session.flush()

    res = filtros_inutiles(db_session, t.id, run)
    assert [r["attribute"] for r in res] == ["color"]
    assert res[0]["valor_dominante"] == "rojo"


def test_filtro_inutil_ignora_no_filtrables_y_poca_evidencia(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "color", filterable=False, sets=[4])  # no filtrable
    _attr(db_session, t, "talla", filterable=True, sets=[4])
    run, part = _perfil(db_session, t)
    db_session.add(ValueStats(partition_id=part.id, attribute_code="color", kind="texto",
                              n_present=100, mode_share=0.99, distinct_values=1, top_values=[]))
    db_session.add(ValueStats(partition_id=part.id, attribute_code="talla", kind="texto",
                              n_present=10, mode_share=0.99, distinct_values=1, top_values=[]))
    db_session.flush()
    assert filtros_inutiles(db_session, t.id, run) == []


# --- filtro perdido -------------------------------------------------------

def test_filtro_perdido_buen_dato_no_filtrable(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "material", filterable=False, sets=[4])  # candidato
    _attr(db_session, t, "color", filterable=True, sets=[4])      # ya filtrable
    run, part = _perfil(db_session, t)
    db_session.add(AttributeCoverage(partition_id=part.id, attribute_code="material",
                                     presente=95, vacio=5, no_aplica=0, desconocido=0,
                                     coverage=0.95))
    db_session.add(AttributeCoverage(partition_id=part.id, attribute_code="color",
                                     presente=99, vacio=1, no_aplica=0, desconocido=0,
                                     coverage=0.99))
    # material discrimina (varios valores) -> no degenerado
    db_session.add(ValueStats(partition_id=part.id, attribute_code="material", kind="texto",
                              n_present=95, mode_share=0.4, distinct_values=8, top_values=[]))
    db_session.flush()

    res = filtros_perdidos(db_session, t.id, run)
    assert [r["attribute"] for r in res] == ["material"]
    assert res[0]["candidato"] is True


def test_filtro_perdido_excluye_atributo_constante(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "condicion", filterable=False, sets=[4])
    run, part = _perfil(db_session, t)
    db_session.add(AttributeCoverage(partition_id=part.id, attribute_code="condicion",
                                     presente=100, vacio=0, no_aplica=0, desconocido=0,
                                     coverage=1.0))
    # un solo valor -> degenerado -> NO es un filtro perdido útil
    db_session.add(ValueStats(partition_id=part.id, attribute_code="condicion", kind="texto",
                              n_present=100, mode_share=1.0, distinct_values=1, top_values=[]))
    db_session.flush()
    assert filtros_perdidos(db_session, t.id, run) == []


def test_filtro_perdido_ignora_baja_cobertura(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "peso", filterable=False, sets=[4])
    run, part = _perfil(db_session, t)
    db_session.add(AttributeCoverage(partition_id=part.id, attribute_code="peso",
                                     presente=40, vacio=60, no_aplica=0, desconocido=0,
                                     coverage=0.40))  # cobertura pobre
    db_session.flush()
    assert filtros_perdidos(db_session, t.id, run) == []
