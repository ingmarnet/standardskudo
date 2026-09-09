import json
from pathlib import Path

import httpx
import pytest

from skudo.ingest.full_sync import full_sync
from skudo.magento.client import MagentoClient
from skudo.mirror.models import Tenant
from skudo.mirror.products import get_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

PAGE_1 = {
    "items": [
        {"sku": "0074", "mpn": "ABC-123/B", "model": "X1", "gtin": None,
         "variant_key": None, "attribute_set_id": 4, "type_id": "simple",
         "global_values": {"name": "Notebook", "weight": "2.1"},
         "store_values": {}, "website_ids": [1], "category_ids": [15],
         "updated_at": "2026-09-01 10:00:00"},
    ],
    "next_cursor": "c2t1ZG8xOjc0",
}
PAGE_2 = {
    "items": [
        {"sku": "SKU2", "mpn": None, "model": None, "gtin": None, "variant_key": None,
         "attribute_set_id": 4, "type_id": "simple",
         "global_values": {"name": "Aire Acondicionado"},
         "store_values": {"name": "Ar Condicionado"},
         "website_ids": [1], "category_ids": [], "updated_at": "2026-09-02 11:00:00"},
    ],
    "next_cursor": None,
}


def make_client() -> MagentoClient:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if request.url.path.endswith("/products"):
            cursor = request.url.params.get("cursor")
            return httpx.Response(200, json=PAGE_2 if cursor else PAGE_1)
        return httpx.Response(404)

    return MagentoClient(
        "https://x.test", "token", transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_full_sync_walks_every_page(db_session, tenant):
    report = full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    assert report.pages_fetched == 2
    assert report.records_written == 2


def test_global_value_is_mirrored_with_global_provenance(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.attributes["name"] == "Notebook"
    assert row.scope_provenance["name"] == "global"


def test_store_override_is_mirrored_with_store_provenance(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "SKU2", 1)
    assert row.attributes["name"] == "Ar Condicionado"
    assert row.scope_provenance["name"] == "store"


def test_identity_survives_the_round_trip(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.sku == "0074"
    assert row.mpn == "ABC-123/B"


def test_full_sync_is_idempotent(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])
    second = full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    from sqlalchemy import func, select

    from skudo.mirror.models import ProductRecord

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == tenant.id
        )
    )
    assert second.records_written == 2
    assert total == 2
