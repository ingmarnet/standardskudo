import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from skudo.ingest.delta_sync import delta_sync
from skudo.ingest.source import TenantSource
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

# Mismo SKU dos veces en una página: cubre las dos direcciones de la colisión
# para probar que gana el último evento (last-event-wins), no "delete" absoluto.
SAVE_THEN_DELETE_SAME_SKU = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU1", "event": "delete",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}

DELETE_THEN_SAVE_SAME_SKU = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "delete",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}


def make_source(tenant_id: int, changes=CHANGES, refreshed=None) -> TenantSource:
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
            return httpx.Response(200, json={"items": refreshed or REFRESHED})
        return httpx.Response(404)

    return TenantSource(
        tenant_id=tenant_id,
        base_url="https://x.test",
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
def seeded(db_session, tenant):
    from datetime import UTC, datetime

    for sku, name in (("SKU1", "Notebook"), ("SKU9", "A borrar")):
        upsert_record(db_session, tenant.id, 1,
                      ProductIdentity(sku=sku), {"name": name}, {"name": "global"},
                      datetime(2026, 9, 1, tzinfo=UTC))
    db_session.flush()
    return tenant


def test_saved_product_is_refreshed(db_session, seeded):
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == "Notebook corregido"


def test_deleted_product_is_removed_from_the_mirror(db_session, seeded):
    report = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU9", 1) is None
    assert report.records_deleted == 1


def test_watermark_advances_and_persists(db_session, seeded):
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    stored = db_session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == seeded.id)
    )
    assert stored == 42


def test_second_run_sees_no_changes(db_session, seeded):
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])
    second = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert second.changes_seen == 0
    assert second.watermark == 42


def test_save_then_delete_same_sku_in_one_page_ends_deleted(db_session, seeded):
    delta_sync(
        db_session, make_source(seeded.id, changes=SAVE_THEN_DELETE_SAME_SKU),
        store_view_ids=[1],
    )

    assert get_record(db_session, seeded.id, "SKU1", 1) is None


def test_delete_then_save_same_sku_in_one_page_ends_refreshed(db_session, seeded):
    delta_sync(
        db_session, make_source(seeded.id, changes=DELETE_THEN_SAVE_SAME_SKU),
        store_view_ids=[1],
    )

    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == "Notebook corregido"


def test_a_replayed_change_does_not_duplicate_records(db_session, seeded):
    from sqlalchemy import func

    from skudo.mirror.models import ProductRecord

    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])
    # Se fuerza el rebobinado del watermark para simular un reintento.
    db_session.execute(
        SyncWatermark.__table__.update()
        .where(SyncWatermark.tenant_id == seeded.id)
        .values(last_change_id=40)
    )
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == seeded.id, ProductRecord.sku == "SKU1"
        )
    )
    assert total == 1


def test_refresh_carries_the_website_ids(db_session, seeded):
    """Los registros sembrados por `seeded` no llevan `website_ids` (no se pasó
    al crearlos): si `delta_sync` no leyera la clave del payload, esta prueba
    seguiría viendo `None` después del refresh en vez de `[1]`."""
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    row = get_record(db_session, seeded.id, "SKU1", 1)
    assert row.website_ids == [1]


def test_refresh_carries_the_attribute_set_and_type(db_session, seeded):
    """Los dos campos tienen que sobrevivir también al camino incremental: si
    solo los leyera `full_sync`, un producto que cambia de set quedaría con el
    set viejo hasta la siguiente carga completa."""
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    row = get_record(db_session, seeded.id, "SKU1", 1)
    assert row.attribute_set_id == 4
    assert row.type_id == "simple"


def test_the_watermark_records_when_we_last_synced_well(db_session, tenant):
    """`sync_watermark.updated_at` es el único registro de 'cuándo sincronizamos
    bien por última vez', justo la magnitud del criterio de SLA de delta. El
    modelo declaraba `onupdate=func.now()`, que NO se aplica a un
    `insert().on_conflict_do_update()` de Core: el valor tiene que ir en el
    `set_` del statement.

    Desigualdad estricta dentro de una sola transacción: solo la satisface
    `clock_timestamp()`. `func.now()` daría dos valores idénticos.
    """
    from skudo.ingest.delta_sync import _write_watermark

    def updated_at():
        return db_session.scalar(
            select(SyncWatermark.updated_at).where(SyncWatermark.tenant_id == tenant.id)
        )

    _write_watermark(db_session, tenant.id, 10)
    first = updated_at()

    _write_watermark(db_session, tenant.id, 20)

    assert updated_at() > first


BAD_DATE_REFRESHED = [
    {**REFRESHED[0], "updated_at": "0000-00-00 00:00:00"},
]


def test_a_poison_pill_date_does_not_block_the_watermark(db_session, seeded):
    """Este es el caso grave: si una fecha ilegible aborta la página, el
    watermark no avanza y NINGUNA sincronización posterior puede progresar. El
    cambio se aplica con la fecha desconocida y el watermark avanza."""
    report = delta_sync(
        db_session, make_source(seeded.id, refreshed=BAD_DATE_REFRESHED),
        store_view_ids=[1],
    )

    row = get_record(db_session, seeded.id, "SKU1", 1)
    assert row.attributes["name"] == "Notebook corregido"
    assert row.magento_updated_at is None
    assert report.watermark == 42
    assert report.records_without_timestamp == 1
    assert report.skus_without_timestamp == ["SKU1"]
