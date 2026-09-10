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


def test_absent_commercial_signals_are_stored_as_unknown(db_session, tenant):
    """`units_sold`, `revenue` y `search_demand` ausentes son 'no tenemos ese
    dato para esta tienda', no cero. Eran NOT NULL, y como `upsert_signals`
    manda siempre la clave con `row.get(key)` -> None, el `default=0` de Python
    no se aplicaba nunca: la fila reventaba con IntegrityError."""
    upsert_signals(db_session, tenant.id, 1, [{"sku": "SIN_DATOS", "uses_msi": False}])

    row = get_signal(db_session, tenant.id, "SIN_DATOS", 1)
    assert row.units_sold is None
    assert row.revenue is None
    assert row.search_demand is None


def test_zero_search_demand_is_not_the_same_as_no_search_data(db_session, tenant):
    """El spec exige incertidumbre visible: 'nadie lo buscó' y 'no tenemos datos
    de búsqueda de esa tienda' no pueden colapsar en el mismo cero, porque el
    primero es una señal comercial y el segundo una laguna del origen."""
    upsert_signals(db_session, tenant.id, 1, [
        {"sku": "NADIE_LO_BUSCA", "search_demand": 0, "uses_msi": False},
        {"sku": "SIN_BUSCADOR", "uses_msi": False},
    ])

    assert get_signal(db_session, tenant.id, "NADIE_LO_BUSCA", 1).search_demand == 0
    assert get_signal(db_session, tenant.id, "SIN_BUSCADOR", 1).search_demand is None


def test_a_known_value_can_replace_an_unknown_one(db_session, tenant):
    """Cuando el dato aparece, deja de ser desconocido."""
    upsert_signals(db_session, tenant.id, 1, [{"sku": "SKU3", "uses_msi": False}])
    upsert_signals(db_session, tenant.id, 1, [
        {"sku": "SKU3", "uses_msi": False, "units_sold": 7, "revenue": 100.0,
         "search_demand": 12},
    ])

    row = get_signal(db_session, tenant.id, "SKU3", 1)
    assert row.units_sold == 7
    assert row.search_demand == 12


# --- M3: una señal no sobrevive a su producto -------------------------------


def _seed_record(db_session, tenant_id: int, sku: str, store_view: int) -> None:
    from datetime import UTC, datetime

    from skudo_testing import upsert_record

    from skudo.mirror.products import ProductIdentity

    upsert_record(
        db_session, tenant_id, store_view, ProductIdentity(sku=sku),
        {"name": sku}, {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_a_signal_of_a_sku_with_no_product_record_is_deleted(db_session, tenant):
    """S1 prioriza los hallazgos POR señal: un SKU fantasma con facturación no
    es un hallazgo fabricado más, es el PRIMERO que un humano ve."""
    from skudo.mirror.signals import delete_orphan_signals

    upsert_signals(db_session, tenant.id, 1, ROWS)

    assert delete_orphan_signals(db_session, tenant.id) == 2
    assert get_signal(db_session, tenant.id, "SKU1", 1) is None


def test_a_signal_of_a_product_mirrored_in_another_store_view_survives(db_session, tenant):
    """La prueba de "demasiado amplio": la señal es POR tienda y el producto
    puede estar espejado sólo en la otra. Sigue siendo describible, así que su
    señal no es huérfana."""
    from skudo.mirror.signals import delete_orphan_signals

    _seed_record(db_session, tenant.id, "SKU1", 3)
    upsert_signals(db_session, tenant.id, 1, ROWS[:1])

    assert delete_orphan_signals(db_session, tenant.id) == 0
    assert get_signal(db_session, tenant.id, "SKU1", 1) is not None


def test_cleaning_one_tenants_signals_never_touches_another_tenants(db_session, tenant):
    """El mismo sku huérfano en los dos tenants: la forma débil —"el otro está
    vacío"— no podría pasar por casualidad."""
    from skudo.mirror.signals import delete_orphan_signals

    other = Tenant(code="otro", name="Otro", base_url="https://y.test", token_env_var="T2")
    db_session.add(other)
    db_session.flush()
    upsert_signals(db_session, tenant.id, 1, ROWS[:1])
    upsert_signals(db_session, other.id, 1, ROWS[:1])

    assert delete_orphan_signals(db_session, tenant.id) == 1
    assert get_signal(db_session, other.id, "SKU1", 1) is not None


def test_a_record_of_another_tenant_does_not_make_a_signal_look_alive(db_session, tenant):
    """La otra dirección del filtro por tenant, la que dejaría la limpieza
    demasiado estrecha: sin el filtro en el `EXISTS`, el producto del otro
    tenant con el mismo sku salvaría a la señal rancia para siempre."""
    from skudo.mirror.signals import delete_orphan_signals

    other = Tenant(code="otro", name="Otro", base_url="https://y.test", token_env_var="T2")
    db_session.add(other)
    db_session.flush()
    _seed_record(db_session, other.id, "SKU1", 1)
    upsert_signals(db_session, tenant.id, 1, ROWS[:1])

    assert delete_orphan_signals(db_session, tenant.id) == 1
