"""C3: el camino de ingesta de `product_signal`.

`/signals` se entregó en la fase de enmienda, pero del lado Python
`MagentoClient` no tenía método `signals()` y `upsert_signals` solo se llamaba
desde tests: la tabla existía, sus pruebas estaban verdes y ni un dato real
llegaba nunca. Es el hallazgo C1 otra vez, en la quinta tabla, reproducido en
la misma fase que lo arreglaba para las otras cuatro.

`tests/mirror/test_write_paths_are_reachable.py` es la comprobación permanente
que hace que la suite lo encuentre la próxima vez; este archivo prueba el
ingestor concreto.
"""

from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response
from sqlalchemy import select

from skudo.ingest.signal_sync import sync_signals
from skudo.ingest.source import TenantSource
from skudo.mirror.models import ProductSignal, Tenant
from skudo.mirror.signals import get_signal

# Señales DISTINTAS por store view a propósito: es la propiedad que el spec
# exige (la señal comercial es de (SKU, tienda), no del SKU) y la única forma
# de que una implementación que ingiera una sola vez y copie el resultado a
# todas las tiendas falle esta prueba.
SIGNALS_BY_STORE = {
    1: [
        {"sku": "SKU1", "units_sold": 312, "revenue": 45000000.0, "salable_qty": 4.0,
         "physical_qty": 9.0, "uses_msi": True, "margin": 0.22, "search_demand": 890},
    ],
    3: [
        {"sku": "SKU1", "units_sold": 7, "revenue": None, "salable_qty": None,
         "physical_qty": 2.0, "uses_msi": True, "margin": None, "search_demand": None},
    ],
}


def make_source(tenant_id: int, by_store=SIGNALS_BY_STORE):
    """Registra además los parámetros de cada petición a `/signals`."""
    asked: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/signals"):
            asked.append(dict(request.url.params))
            store_id = int(request.url.params["storeId"])
            return skudo_response({"items": by_store.get(store_id, [])})
        return httpx.Response(404)

    source = TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )
    return source, asked


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_signals_are_ingested_per_store_view(db_session, tenant):
    """Discrimina sobre los VALORES, y son distintos entre tiendas: una ingesta
    que pidiera una sola store view y replicara el resultado dejaría 312 en las
    dos."""
    source, asked = make_source(tenant.id)

    report = sync_signals(db_session, source, store_view_ids=[1, 3])

    assert [a["storeId"] for a in asked] == ["1", "3"]
    assert report.signals_written == 2
    assert get_signal(db_session, tenant.id, "SKU1", 1).units_sold == 312
    assert get_signal(db_session, tenant.id, "SKU1", 3).units_sold == 7


def test_unknown_measurements_stay_null_through_the_ingest(db_session, tenant):
    """El módulo distingue "cero real" de "no lo sabemos" (`revenue` con NULLs
    parciales, `search_demand` de una tienda sin datos de búsqueda). Si el
    ingestor los colapsara en 0 al escribir, esa distinción moriría acá.

    Discrimina porque la store view 3 llega con `revenue`, `salable_qty`,
    `margin` y `search_demand` nulos y `physical_qty` 2.0: un `or 0` los
    volvería 0.0 y este test fallaría en cuatro aserciones."""
    source, _ = make_source(tenant.id)

    sync_signals(db_session, source, store_view_ids=[1, 3])

    row = get_signal(db_session, tenant.id, "SKU1", 3)
    assert row.revenue is None
    assert row.salable_qty is None
    assert row.margin is None
    assert row.search_demand is None
    assert row.physical_qty == 2.0


def test_the_window_in_days_travels_to_the_module(db_session, tenant):
    source, asked = make_source(tenant.id)

    sync_signals(db_session, source, store_view_ids=[1], days=30)

    assert asked[0]["days"] == "30"


def test_a_store_view_without_signals_writes_nothing_for_it(db_session, tenant):
    """Una tienda sin ventas en la ventana no es un error: escribe cero filas,
    y no debe dejar filas de OTRA tienda contaminadas."""
    source, _ = make_source(tenant.id, by_store={1: SIGNALS_BY_STORE[1]})

    report = sync_signals(db_session, source, store_view_ids=[1, 3])

    assert report.signals_written == 1
    assert db_session.scalars(
        select(ProductSignal.store_view_magento_id).where(
            ProductSignal.tenant_id == tenant.id
        )
    ).all() == [1]


def test_reingesting_refreshes_instead_of_duplicating(db_session, tenant):
    source, _ = make_source(tenant.id)
    sync_signals(db_session, source, store_view_ids=[1])

    updated = {1: [{**SIGNALS_BY_STORE[1][0], "units_sold": 400}]}
    source2, _ = make_source(tenant.id, by_store=updated)
    sync_signals(db_session, source2, store_view_ids=[1])

    assert get_signal(db_session, tenant.id, "SKU1", 1).units_sold == 400
    assert len(db_session.scalars(
        select(ProductSignal.id).where(ProductSignal.tenant_id == tenant.id)
    ).all()) == 1
