"""Aislamiento por tenant en los caminos de LECTURA, con dos tenants poblados.

La forma débil de este test —poblar el tenant A y afirmar que el B está vacío—
solo comprueba que una tabla vacía está vacía, y seguiría pasando si alguien
quitara un filtro `tenant_id`. Aquí se siembra EL MISMO SKU bajo dos tenants con
contenido distinto, así que una consulta a la que le falte el filtro devuelve la
fila del otro cliente y el test falla ruidosamente.

Es el modo de fallo que acabaría con el negocio: el catálogo de un cliente
visible dentro del espejo de otro.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import checksums_payload, skudo_response, upsert_record

from skudo.ingest.reconcile import reconcile
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, get_record
from skudo.mirror.signals import get_signal, upsert_signals

SHARED_SKU = "SKU-COMPARTIDO"
STORE_VIEW = 1


def make_source(tenant_id: int, skus: list[str]) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/checksums"):
            return skudo_response(checksums_payload(skus))
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def two_tenants(db_session):
    """Dos tenants con el MISMO sku y contenido distinto en cada uno."""
    a = Tenant(code="a", name="Tenant A", base_url="https://a.test", token_env_var="TA")
    b = Tenant(code="b", name="Tenant B", base_url="https://b.test", token_env_var="TB")
    db_session.add_all([a, b])
    db_session.flush()

    for tenant, name in ((a, "Notebook de A"), (b, "Notebook de B")):
        upsert_record(
            db_session, tenant.id, STORE_VIEW, ProductIdentity(sku=SHARED_SKU),
            {"name": name}, {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC),
        )
    # El tenant B tiene además un SKU que A no tiene: si una lectura de A se
    # colara al espejo de B, los conteos de A saldrían inflados.
    upsert_record(
        db_session, b.id, STORE_VIEW, ProductIdentity(sku="SOLO-DE-B"),
        {"name": "Solo de B"}, {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC),
    )

    upsert_signals(db_session, a.id, STORE_VIEW,
                   [{"sku": SHARED_SKU, "units_sold": 11, "uses_msi": False}])
    upsert_signals(db_session, b.id, STORE_VIEW,
                   [{"sku": SHARED_SKU, "units_sold": 22, "uses_msi": False}])
    db_session.flush()
    return a, b


def test_get_record_returns_only_the_tenants_own_row(db_session, two_tenants):
    a, b = two_tenants

    assert get_record(db_session, a.id, SHARED_SKU, STORE_VIEW).attributes["name"] == (
        "Notebook de A"
    )
    assert get_record(db_session, b.id, SHARED_SKU, STORE_VIEW).attributes["name"] == (
        "Notebook de B"
    )


def test_a_sku_of_one_tenant_is_invisible_to_the_other(db_session, two_tenants):
    a, _b = two_tenants

    assert get_record(db_session, a.id, "SOLO-DE-B", STORE_VIEW) is None


def test_get_signal_returns_only_the_tenants_own_row(db_session, two_tenants):
    a, b = two_tenants

    assert get_signal(db_session, a.id, SHARED_SKU, STORE_VIEW).units_sold == 11
    assert get_signal(db_session, b.id, SHARED_SKU, STORE_VIEW).units_sold == 22


def test_reconcile_counts_only_the_tenants_own_rows(db_session, two_tenants):
    """A tiene un producto y B dos. Si `reconcile` no filtrara por tenant, el
    conteo del espejo de A saldría 3 y reportaría una deriva inexistente —o, en
    el sentido peligroso, taparía una real."""
    a, b = two_tenants

    report_a = reconcile(db_session, make_source(a.id, [SHARED_SKU]), STORE_VIEW)
    report_b = reconcile(
        db_session, make_source(b.id, [SHARED_SKU, "SOLO-DE-B"]), STORE_VIEW
    )

    assert report_a.mirror_count == 1
    assert report_a.digest_matches is True
    assert report_a.needs_full_sync is False

    assert report_b.mirror_count == 2
    assert report_b.digest_matches is True
    assert report_b.needs_full_sync is False


def test_drift_in_one_tenant_is_not_reported_in_the_other(db_session, two_tenants):
    a, b = two_tenants

    report_a = reconcile(db_session, make_source(a.id, [SHARED_SKU]), STORE_VIEW)
    report_b = reconcile(db_session, make_source(b.id, [SHARED_SKU]), STORE_VIEW)

    assert report_a.needs_full_sync is False
    assert report_b.needs_full_sync is True
