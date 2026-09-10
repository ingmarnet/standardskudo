import pytest

from skudo.mirror.attributes import (
    distinct_option_ids,
    option_labels,
    upsert_attribute,
    upsert_option,
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
