from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from skudo.magento.client import MagentoClient
from skudo.mirror.categories import assign_product
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record
from skudo.mirror.topology import sync_topology


class FullSyncReport(BaseModel):
    records_written: int = 0
    pages_fetched: int = 0


def parse_magento_datetime(raw: str) -> datetime:
    """Magento devuelve 'YYYY-MM-DD HH:MM:SS' en UTC, sin zona explícita."""
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def full_sync(
    session: Session,
    client: MagentoClient,
    tenant_id: int,
    store_view_ids: list[int],
) -> FullSyncReport:
    """Carga completa del catálogo, una pasada por store view.

    Se recorre por store view porque los valores de override viven en ese scope:
    una sola pasada global no permitiría saber qué heredó cada tienda.
    """
    profile = client.environment()
    sync_topology(session, tenant_id, profile)

    report = FullSyncReport()

    for store_id in store_view_ids:
        for page in client.iter_products(store_id):
            report.pages_fetched += 1
            for item in page["items"]:
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
                upsert_record(
                    session,
                    tenant_id,
                    store_id,
                    identity,
                    effective,
                    provenance,
                    parse_magento_datetime(item["updated_at"]),
                )
                for category_id in item["category_ids"]:
                    assign_product(session, tenant_id, item["sku"], category_id)
                report.records_written += 1

    session.commit()
    return report
