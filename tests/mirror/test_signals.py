import pytest

from skudo.mirror.models import Tenant
from skudo.mirror.signals import get_signal, upsert_signals


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


ROWS = [
    {"sku": "SKU1", "units_sold": 312, "revenue": 45000000.0, "salable_qty": 4.0,
     "physical_qty": 9.0, "uses_msi": True, "margin": 0.22, "search_demand": 890},
    {"sku": "SKU2", "units_sold": 0, "revenue": 0.0, "salable_qty": 0.0,
     "physical_qty": 0.0, "uses_msi": True, "margin": None, "search_demand": 3},
]


def test_signals_are_stored_per_store_view(db_session, tenant):
    assert upsert_signals(db_session, tenant.id, 1, ROWS) == 2

    row = get_signal(db_session, tenant.id, "SKU1", 1)
    assert row.units_sold == 312
    assert get_signal(db_session, tenant.id, "SKU1", 2) is None


def test_salable_quantity_is_kept_apart_from_physical(db_session, tenant):
    """Con MSI, lo que se puede vender no es lo que hay en el almacén: la
    diferencia son reservas y pedidos pendientes. Priorizar por cantidad física
    haría enriquecer productos que en realidad no se pueden vender."""
    upsert_signals(db_session, tenant.id, 1, ROWS)

    row = get_signal(db_session, tenant.id, "SKU1", 1)
    assert row.salable_qty == 4.0
    assert row.physical_qty == 9.0
    assert row.uses_msi is True


def test_missing_margin_is_preserved_as_unknown(db_session, tenant):
    """Margen ausente es 'desconocido', no cero. Colapsarlo falsearía la
    priorización comercial."""
    upsert_signals(db_session, tenant.id, 1, ROWS)

    assert get_signal(db_session, tenant.id, "SKU2", 1).margin is None


def test_upsert_replaces_previous_window(db_session, tenant):
    upsert_signals(db_session, tenant.id, 1, ROWS)
    upsert_signals(db_session, tenant.id, 1, [{**ROWS[0], "units_sold": 400}])

    assert get_signal(db_session, tenant.id, "SKU1", 1).units_sold == 400


def test_upsert_refreshes_observed_at(db_session, tenant):
    upsert_signals(db_session, tenant.id, 1, ROWS)
    first_observed_at = get_signal(db_session, tenant.id, "SKU1", 1).observed_at

    upsert_signals(db_session, tenant.id, 1, [{**ROWS[0], "units_sold": 400}])
    second_observed_at = get_signal(db_session, tenant.id, "SKU1", 1).observed_at

    assert second_observed_at > first_observed_at
