from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.ingest.full_sync import note_unreadable_timestamp, parse_magento_datetime
from skudo.magento.client import MagentoClient
from skudo.mirror.models import ProductRecord, SyncWatermark
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record


class DeltaSyncReport(BaseModel):
    changes_seen: int = 0
    records_updated: int = 0
    records_deleted: int = 0
    watermark: int = 0
    records_without_timestamp: int = 0
    skus_without_timestamp: list[str] = []


def _read_watermark(session: Session, tenant_id: int) -> int:
    value = session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == tenant_id)
    )
    return value or 0


def _write_watermark(session: Session, tenant_id: int, change_id: int) -> None:
    """`updated_at` se escribe explícitamente en los `values` y en el `set_`.

    Un `onupdate=` del modelo NO se aplica a un `insert().on_conflict_do_update()`
    de Core, así que declararlo allí y confiar en él dejaba la columna congelada
    en la hora del primer insert. Y es `clock_timestamp()` y no `now()` porque
    `now()` es de alcance transaccional en Postgres: dos avances de watermark en
    la misma transacción registrarían la misma hora.
    """
    stmt = insert(SyncWatermark).values(
        tenant_id=tenant_id,
        last_change_id=change_id,
        updated_at=func.clock_timestamp(),
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={
                "last_change_id": stmt.excluded.last_change_id,
                "updated_at": func.clock_timestamp(),
            },
        )
    )
    session.flush()


def delta_sync(
    session: Session,
    client: MagentoClient,
    tenant_id: int,
    store_view_ids: list[int],
) -> DeltaSyncReport:
    """Aplica los cambios pendientes desde el último watermark.

    El watermark solo avanza cuando la página se aplicó por completo: si algo
    falla a mitad, el reintento vuelve a traer esos cambios. Reaplicar un cambio
    es inofensivo porque todo el camino es upsert por
    (tenant, sku, store_view).
    """
    report = DeltaSyncReport(watermark=_read_watermark(session, tenant_id))

    for page in client.iter_deltas(report.watermark):
        # Un SKU puede aparecer varias veces en la misma página; solo interesa
        # su último estado, y gana el último evento, sea cual sea (last-event-wins),
        # no "delete" de forma absoluta: un delete seguido de un save significa que
        # el SKU se borró y se volvió a crear, y ahí debe ganar el save. Esto solo
        # es correcto porque el endpoint de deltas garantiza los items en orden
        # ascendente de change_id; no se ordena aquí a propósito, para que una
        # regresión real de esa garantía se note en vez de quedar oculta.
        last_event: dict[str, str] = {}
        for change in page["items"]:
            report.changes_seen += 1
            last_event[change["sku"]] = change["event"]

        to_delete = [sku for sku, event in last_event.items() if event == "delete"]
        to_refresh = [sku for sku, event in last_event.items() if event != "delete"]

        if to_delete:
            result = session.execute(
                delete(ProductRecord).where(
                    ProductRecord.tenant_id == tenant_id,
                    ProductRecord.sku.in_(to_delete),
                )
            )
            report.records_deleted += result.rowcount or 0

        for store_id in store_view_ids:
            for item in client.products_by_sku(store_id, to_refresh):
                identity = ProductIdentity(
                    sku=item["sku"],
                    mpn=item.get("mpn"),
                    model=item.get("model"),
                    gtin=item.get("gtin"),
                    variant_key=item.get("variant_key"),
                )
                effective, provenance = resolve_scope(
                    item["global_values"], item["store_values"]
                )
                magento_updated_at = parse_magento_datetime(item.get("updated_at"))
                if magento_updated_at is None:
                    note_unreadable_timestamp(report, item["sku"])
                upsert_record(
                    session, tenant_id, store_id, identity, effective, provenance,
                    magento_updated_at,
                    attribute_set_id=item.get("attribute_set_id"),
                    type_id=item.get("type_id"),
                )
                report.records_updated += 1

        report.watermark = page["last_change_id"]
        _write_watermark(session, tenant_id, report.watermark)
        session.commit()

    return report
