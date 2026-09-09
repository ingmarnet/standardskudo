from datetime import UTC, datetime

import pytest

from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, get_record, resolve_scope, upsert_record


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_store_value_wins_and_is_marked_as_store():
    effective, provenance = resolve_scope(
        global_values={"name": "Notebook", "color": "17"},
        store_values={"name": "Notebook BR"},
    )
    assert effective == {"name": "Notebook BR", "color": "17"}
    assert provenance == {"name": "store", "color": "global"}


def test_absent_store_value_falls_back_to_global():
    effective, provenance = resolve_scope({"weight": "2.1"}, {})
    assert effective == {"weight": "2.1"}
    assert provenance == {"weight": "global"}


def test_empty_string_at_store_scope_is_still_a_store_value():
    """Un valor vacío puesto a propósito en la store view NO es herencia.
    Colapsarlo con 'global' ocultaría un defecto real de traducción."""
    effective, provenance = resolve_scope({"name": "Notebook"}, {"name": ""})
    assert effective == {"name": ""}
    assert provenance == {"name": "store"}


def test_identity_preserves_leading_zeros_and_suffixes(db_session, tenant):
    identity = ProductIdentity(sku="0074", mpn="ABC-123/B", model="X1", gtin="07501234567890")
    upsert_record(db_session, tenant.id, 1, identity, {"name": "N"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.sku == "0074"
    assert row.mpn == "ABC-123/B"
    assert row.gtin == "07501234567890"


def test_the_same_product_has_one_record_per_store_view(db_session, tenant):
    identity = ProductIdentity(sku="SKU1", mpn=None, model=None, gtin=None)
    for store_id, name in ((1, "Aire Acondicionado"), (2, "Ar Condicionado")):
        upsert_record(db_session, tenant.id, store_id, identity, {"name": name},
                      {"name": "store"}, datetime(2026, 9, 1, tzinfo=UTC))

    assert get_record(db_session, tenant.id, "SKU1", 1).attributes["name"] == "Aire Acondicionado"
    assert get_record(db_session, tenant.id, "SKU1", 2).attributes["name"] == "Ar Condicionado"


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
