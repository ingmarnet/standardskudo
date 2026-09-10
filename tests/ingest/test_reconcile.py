from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response

from skudo.ingest.reconcile import reconcile, sku_digest
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, upsert_record


def make_source(tenant_id: int, count: int, digest: str) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/checksums"):
            return skudo_response({"product_count": count, "sku_digest": digest})
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def mirrored(db_session, tenant):
    from datetime import UTC, datetime

    for sku in ("SKU1", "SKU2"):
        upsert_record(db_session, tenant.id, 1, ProductIdentity(sku=sku),
                      {"name": sku}, {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC))
    db_session.flush()
    return tenant


def test_digest_is_order_independent():
    assert sku_digest(["SKU2", "SKU1"]) == sku_digest(["SKU1", "SKU2"])


def test_digest_changes_when_a_sku_is_missing():
    assert sku_digest(["SKU1", "SKU2"]) != sku_digest(["SKU1"])


def test_no_drift_when_count_and_digest_match(db_session, mirrored):
    report = reconcile(
        db_session, make_source(mirrored.id, 2, sku_digest(["SKU1", "SKU2"])), 1
    )

    assert report.digest_matches is True
    assert report.needs_full_sync is False


def test_drift_detected_when_magento_has_more_products(db_session, mirrored):
    report = reconcile(
        db_session, make_source(mirrored.id, 3, "cualquier-otro-digest"), 1
    )

    assert report.magento_count == 3
    assert report.mirror_count == 2
    assert report.needs_full_sync is True


def test_drift_detected_when_counts_match_but_content_differs(db_session, mirrored):
    """El conteo puede coincidir y el contenido no: un SKU borrado y otro creado
    en el mismo intervalo. Por eso hace falta el digest y no solo contar."""
    report = reconcile(
        db_session, make_source(mirrored.id, 2, sku_digest(["SKU1", "SKU3"])), 1
    )

    assert report.magento_count == report.mirror_count
    assert report.digest_matches is False
    assert report.needs_full_sync is True
