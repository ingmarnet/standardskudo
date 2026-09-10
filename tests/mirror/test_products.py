from datetime import UTC, datetime

import pytest

from skudo.mirror.models import Tenant
from skudo.mirror.products import (
    ProductIdentity,
    content_hash,
    get_record,
    resolve_scope,
    upsert_record,
)


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


# El scope declarado de cada atributo, tal como lo espeja `sync_attributes`
# desde `eav_attribute.is_global` (1 -> global, 2 -> website, 0 -> store).
DECLARED = {"name": "store", "color": "store", "price": "website", "weight": "global"}


def test_store_value_wins_and_is_marked_as_store():
    effective, provenance = resolve_scope(
        global_values={"name": "Notebook", "color": "17"},
        store_values={"name": "Notebook BR"},
        declared_scopes=DECLARED,
    )
    assert effective == {"name": "Notebook BR", "color": "17"}
    assert provenance == {"name": "store", "color": "global"}


def test_absent_store_value_falls_back_to_global():
    effective, provenance = resolve_scope({"weight": "2.1"}, {}, DECLARED)
    assert effective == {"weight": "2.1"}
    assert provenance == {"weight": "global"}


def test_empty_string_at_store_scope_is_still_a_store_value():
    """Un valor vacío puesto a propósito en la store view NO es herencia.
    Colapsarlo con 'global' ocultaría un defecto real de traducción."""
    effective, provenance = resolve_scope({"name": "Notebook"}, {"name": ""}, DECLARED)
    assert effective == {"name": ""}
    assert provenance == {"name": "store"}


# --- H2: la procedencia tiene TRES escalas, no dos -------------------------
#
# Magento persiste un atributo con scope de WEBSITE escribiendo una fila EAV
# para CADA store view de ese website. Desde `catalog_product_entity_*` esas
# filas son indistinguibles de un override de tienda, así que la única forma de
# saber en qué escala se fijó el valor es el `declared_scope` del atributo —que
# `sync_attributes` ya espejaba y nadie leía.
#
# No es cosmético: en el catálogo de referencia hay 24 atributos de producto
# con `is_global = 2`, `price` entre ellos porque `catalog/price/scope` está en
# Website. Reportarlos como procedencia "store" le daría a S3 una respuesta con
# confianza equivocada sobre dónde corregir, y el spec §5.6 nombra corregir en
# el scope equivocado como el error de write-back más común de Magento.


def test_an_override_of_a_website_scoped_attribute_is_website_not_store():
    """Discrimina sobre el VALOR: la fila EAV es idéntica a la de un atributo
    de tienda, y solo `declared_scopes` distingue las dos."""
    _, provenance = resolve_scope(
        {"price": "100", "name": "Notebook"},
        {"price": "90", "name": "Notebook BR"},
        DECLARED,
    )
    assert provenance == {"price": "website", "name": "store"}


def test_an_override_of_an_attribute_we_have_not_mirrored_is_unknown():
    """Sin el atributo en el espejo no se sabe en qué escala se fijó el valor.
    "store" sería una respuesta confiada y posiblemente falsa —el riesgo que la
    sección 1 del spec nombra como el mayor del sistema—, así que se dice
    DESCONOCIDO. Es también la señal de que `sync_attributes` no corrió antes
    que la ingesta de productos."""
    _, provenance = resolve_scope({"custom": "a"}, {"custom": "b"}, DECLARED)
    assert provenance == {"custom": "desconocido"}


def test_an_override_of_a_globally_scoped_attribute_is_unknown_not_global():
    """Un atributo declarado global no debería tener fila de store view. Si la
    tiene, el origen se contradice: el valor efectivo NO es el global (la fila
    de tienda existe y gana), pero la escala en la que se fijó tampoco es la
    tienda. Decir "global" mentiría sobre el valor y decir "store" mentiría
    sobre la escala."""
    _, provenance = resolve_scope({"weight": "2.1"}, {"weight": "3.0"}, DECLARED)
    assert provenance == {"weight": "desconocido"}


def test_without_a_scope_map_no_override_claims_a_scale():
    """Un llamador que no pasa el mapa no sabe nada de escalas, y el resultado
    lo refleja. Si el default siguiera siendo "store", toda la ingesta previa a
    `sync_attributes` afirmaría una escala inventada para cada override."""
    _, provenance = resolve_scope({"name": "N"}, {"name": "N BR"})
    assert provenance == {"name": "desconocido"}


def test_identity_preserves_leading_zeros_and_suffixes(db_session, tenant):
    identity = ProductIdentity(sku="0074", mpn="ABC-123/B", model="X1", gtin="07501234567890")
    upsert_record(db_session, tenant.id, 1, identity, {"name": "N"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.sku == "0074"
    assert row.mpn == "ABC-123/B"
    assert row.gtin == "07501234567890"


def test_website_ids_survive_the_round_trip(db_session, tenant):
    """Sin esta columna, `derive_category_effect` no puede evaluar su tercera
    condición: en el tenant piloto es la única que distingue PY de BR, porque
    ambas store views comparten `root_category_id`."""
    identity = ProductIdentity(sku="SKU1")
    upsert_record(db_session, tenant.id, 1, identity, {"name": "N"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC), website_ids=[1])

    row = get_record(db_session, tenant.id, "SKU1", 1)
    assert row.website_ids == [1]


def test_website_ids_is_null_when_not_supplied(db_session, tenant):
    """Ausencia declarada, no un cero ni una lista vacía con aspecto confiable:
    el llamador no informó websites, y NULL lo dice sin inventar un dato."""
    identity = ProductIdentity(sku="SKU1")
    upsert_record(db_session, tenant.id, 1, identity, {"name": "N"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))

    row = get_record(db_session, tenant.id, "SKU1", 1)
    assert row.website_ids is None


def test_the_same_product_has_one_record_per_store_view(db_session, tenant):
    identity = ProductIdentity(sku="SKU1", mpn=None, model=None, gtin=None)
    for store_id, name in ((1, "Aire Acondicionado"), (3, "Ar Condicionado")):
        upsert_record(db_session, tenant.id, store_id, identity, {"name": name},
                      {"name": "store"}, datetime(2026, 9, 1, tzinfo=UTC))

    assert get_record(db_session, tenant.id, "SKU1", 1).attributes["name"] == "Aire Acondicionado"
    assert get_record(db_session, tenant.id, "SKU1", 3).attributes["name"] == "Ar Condicionado"


def test_upsert_replaces_and_updates_the_content_hash(db_session, tenant):
    identity = ProductIdentity(sku="SKU1", mpn=None, model=None, gtin=None)
    upsert_record(db_session, tenant.id, 1, identity, {"name": "A"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))
    first = get_record(db_session, tenant.id, "SKU1", 1).content_hash

    upsert_record(db_session, tenant.id, 1, identity, {"name": "B"}, {"name": "global"},
                  datetime(2026, 9, 2, tzinfo=UTC))
    row = get_record(db_session, tenant.id, "SKU1", 1)

    assert row.attributes["name"] == "B"
    assert row.content_hash != first


def test_upsert_refreshes_mirrored_at(db_session, tenant):
    """`mirrored_at` es 'cuándo lo vimos por última vez'. Si no se refresca,
    miente sobre la frescura del espejo.

    La desigualdad es estricta y el test corre dentro de una sola transacción:
    eso solo lo puede satisfacer `clock_timestamp()`. Con `func.now()`, que en
    Postgres es de alcance transaccional, los dos valores serían idénticos y el
    test pasaría o fallaría por la razón equivocada.
    """
    from sqlalchemy import select

    from skudo.mirror.models import ProductRecord

    def mirrored_at():
        return db_session.scalar(
            select(ProductRecord.mirrored_at).where(
                ProductRecord.tenant_id == tenant.id,
                ProductRecord.sku == "SKU1",
                ProductRecord.store_view_magento_id == 1,
            )
        )

    identity = ProductIdentity(sku="SKU1")
    upsert_record(db_session, tenant.id, 1, identity, {"name": "A"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))
    first = mirrored_at()

    upsert_record(db_session, tenant.id, 1, identity, {"name": "B"}, {"name": "global"},
                  datetime(2026, 9, 2, tzinfo=UTC))

    assert mirrored_at() > first


def test_identical_content_in_two_store_views_hashes_differently(db_session, tenant):
    """Texto idéntico en PY y BR es precisamente el defecto de "nombre sin
    traducir", y el veredicto correcto difiere por store view. Un caché de la
    capa IA con la misma clave para las dos devolvería el veredicto de la tienda
    española para la portuguesa, justo en la población que más hay que juzgar.
    """
    identity = ProductIdentity(sku="SKU1")
    for store_id in (1, 3):
        upsert_record(db_session, tenant.id, store_id, identity, {"name": "Notebook"},
                      {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC))

    py = get_record(db_session, tenant.id, "SKU1", 1).content_hash
    br = get_record(db_session, tenant.id, "SKU1", 3).content_hash
    assert py != br


def test_the_hash_is_stable_for_the_same_store_view_and_content():
    """Sigue siendo un caché: mismo contenido y misma tienda, misma clave."""
    identity = ProductIdentity(sku="SKU1")
    assert content_hash(identity, {"name": "Notebook"}, 1) == content_hash(
        identity, {"name": "Notebook"}, 1
    )
