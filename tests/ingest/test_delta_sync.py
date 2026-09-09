import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from skudo.ingest.delta_sync import delta_sync
from skudo.magento.client import MagentoClient
from skudo.mirror.models import SyncWatermark, Tenant
from skudo.mirror.products import ProductIdentity, get_record, upsert_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

CHANGES = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU9", "event": "delete",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}

REFRESHED = [
    {"sku": "SKU1", "mpn": None, "model": None, "gtin": None, "variant_key": None,
     "attribute_set_id": 4, "type_id": "simple",
     "global_values": {"name": "Notebook corregido"}, "store_values": {},
     "website_ids": [1], "category_ids": [], "updated_at": "2026-09-05 08:00:00"},
]


def make_client(changes=CHANGES) -> MagentoClient:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if path.endswith("/deltas"):
            since = int(request.url.params.get("sinceId", 0))
            if since >= (changes["last_change_id"] or 0):
                return httpx.Response(200, json={"items": [], "last_change_id": None})
            return httpx.Response(200, json=changes)
        if path.endswith("/products-by-sku"):
            return httpx.Response(200, json={"items": REFRESHED})
        return httpx.Response(404)

    return MagentoClient("https://x.test", "t", transport=httpx.MockTransport(handler))


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def seeded(db_session, tenant):
    from datetime import UTC, datetime

    for sku, name in (("SKU1", "Notebook"), ("SKU9", "A borrar")):
        upsert_record(db_session, tenant.id, 1,
                      ProductIdentity(sku=sku), {"name": name}, {"name": "global"},
                      datetime(2026, 9, 1, tzinfo=UTC))
    db_session.flush()
    return tenant


def test_saved_product_is_refreshed(db_session, seeded):
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == "Notebook corregido"


def test_deleted_product_is_removed_from_the_mirror(db_session, seeded):
    report = delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU9", 1) is None
    assert report.records_deleted == 1


def test_watermark_advances_and_persists(db_session, seeded):
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    stored = db_session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == seeded.id)
    )
    assert stored == 42


def test_second_run_sees_no_changes(db_session, seeded):
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])
    second = delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    assert second.changes_seen == 0
    assert second.watermark == 42


def test_a_replayed_change_does_not_duplicate_records(db_session, seeded):
    from sqlalchemy import func

    from skudo.mirror.models import ProductRecord

    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])
    # Se fuerza el rebobinado del watermark para simular un reintento.
    db_session.execute(
        SyncWatermark.__table__.update()
        .where(SyncWatermark.tenant_id == seeded.id)
        .values(last_change_id=40)
    )
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == seeded.id, ProductRecord.sku == "SKU1"
        )
    )
    assert total == 1
