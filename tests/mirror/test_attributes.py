import pytest
from skudo_testing import upsert_option

from skudo.mirror.attributes import (
    distinct_option_ids,
    option_labels,
    upsert_attribute,
)
from skudo.mirror.models import Tenant

# Estas pruebas ejercen las funciones de escritura del espejo directamente, no
# una pasada. El sello es obligatorio desde M3 —una fila sin sellar la barre la
# próxima pasada completa— así que se pasa un valor fijo y explícito: acá no
# hay pasada que lo tome de la secuencia.
PASS_GENERATION = 1


@pytest.fixture
def tenant(db_session):
    row = Tenant(
        code="nissei", name="Nissei", base_url="https://example.test", token_env_var="T"
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def color_attribute(db_session, tenant):
    upsert_attribute(
        db_session,
        tenant.id,
        {
            "code": "color",
            "label": "Color",
            "frontend_input": "select",
            "declared_scope": "store",
            "is_filterable": True,
            "is_required": False,
            "attribute_set_ids": [4],
        },
        sync_generation=PASS_GENERATION,
    )
    return "color"


def test_one_option_with_two_translations_is_one_option(db_session, tenant, color_attribute):
    """Negro (store 1) y Preto (store 2) son la MISMA opción.

    Este es el test que impide la corrección destructiva más peligrosa del
    sistema: consolidar dos etiquetas que en realidad son una traducción.
    """
    upsert_option(
        db_session,
        tenant.id,
        "color",
        option_id=17,
        labels={0: "Negro", 1: "Negro", 3: "Preto"},
        sync_generation=PASS_GENERATION,
    )

    assert distinct_option_ids(db_session, tenant.id, "color") == [17]
    assert option_labels(db_session, tenant.id, "color", 17) == {
        0: "Negro",
        1: "Negro",
        3: "Preto",
    }


def test_two_real_options_stay_separate(db_session, tenant, color_attribute):
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro", 3: "Preto"}, sync_generation=PASS_GENERATION)
    upsert_option(db_session, tenant.id, "color", 18, {0: "Blanco", 3: "Branco"}, sync_generation=PASS_GENERATION)

    assert distinct_option_ids(db_session, tenant.id, "color") == [17, 18]


def test_same_label_in_two_options_is_not_merged(db_session, tenant, color_attribute):
    """Dos opciones distintas pueden compartir etiqueta por error de datos.
    Siguen siendo dos opciones: la identidad es el option_id."""
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro"}, sync_generation=PASS_GENERATION)
    upsert_option(db_session, tenant.id, "color", 99, {0: "Negro"}, sync_generation=PASS_GENERATION)

    assert distinct_option_ids(db_session, tenant.id, "color") == [17, 99]


def test_relabeling_an_option_updates_in_place(db_session, tenant, color_attribute):
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro", 3: "Preto"}, sync_generation=PASS_GENERATION)
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro mate", 3: "Preto mate"}, sync_generation=PASS_GENERATION)

    assert option_labels(db_session, tenant.id, "color", 17) == {
        0: "Negro mate",
        3: "Preto mate",
    }


def test_filterable_flag_is_stored_on_the_attribute(db_session, tenant, color_attribute):
    from sqlalchemy import select

    from skudo.mirror.models import Attribute

    row = db_session.scalar(
        select(Attribute).where(Attribute.tenant_id == tenant.id, Attribute.code == "color")
    )
    # is_filterable es propiedad del ATRIBUTO en Magento, no de la categoría.
    assert row.is_filterable is True


# --- el lote de opciones y el límite de parámetros de Postgres --------------


def test_a_batch_larger_than_the_parameter_limit_is_written(db_session, tenant):
    """La primera versión del lote escribía la página entera en UNA sentencia y
    reventaba contra el catálogo real: el protocolo de Postgres admite 65.535
    parámetros por sentencia y el insert de opciones manda 4 por fila, así que
    más de 16.383 opciones fallan con `number of parameters must be between 0
    and 65535`. No lo vio ninguna prueba —los lotes de la suite eran de dos
    opciones— sino la medición contra la instancia de desarrollo, a mitad de
    una pasada.

    Esta prueba es esa medición, en la suite: un lote del doble del límite.
    """
    from skudo.mirror.attributes import POSTGRES_MAX_BIND_PARAMS, upsert_options

    demasiadas = (POSTGRES_MAX_BIND_PARAMS // 4) * 2
    upsert_attribute(
        db_session, tenant.id,
        {"code": "talle", "label": "Talle", "frontend_input": "select",
         "declared_scope": "global", "is_filterable": True, "is_required": False,
         "attribute_set_ids": [4]},
        sync_generation=PASS_GENERATION,
    )

    written = upsert_options(
        db_session,
        tenant.id,
        [
            {"attribute_code": "talle", "option_id": option_id, "labels": {0: f"t{option_id}"}}
            for option_id in range(demasiadas)
        ],
        sync_generation=PASS_GENERATION,
    )

    assert written == demasiadas
    assert len(distinct_option_ids(db_session, tenant.id, "talle")) == demasiadas
    assert option_labels(db_session, tenant.id, "talle", demasiadas - 1) == {
        0: f"t{demasiadas - 1}"
    }


def test_the_batch_size_leaves_margin_under_the_parameter_limit():
    """El porqué del número, afirmado en vez de comentado: si alguien sube el
    lote buscando velocidad, esto falla antes de que lo haga una pasada."""
    from skudo.mirror.attributes import (
        OPTION_BATCH_SIZE,
        POSTGRES_MAX_BIND_PARAMS,
    )

    # 4 columnas por opción en el insert que más parámetros manda.
    assert OPTION_BATCH_SIZE * 4 < POSTGRES_MAX_BIND_PARAMS
