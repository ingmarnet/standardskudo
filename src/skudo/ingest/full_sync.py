from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Sequence, delete, select
from sqlalchemy.orm import Session

from skudo.magento.client import MagentoClient
from skudo.mirror.categories import set_product_categories
from skudo.mirror.models import ProductRecord
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record
from skudo.mirror.topology import sync_topology

# Secuencia de la base: cada pasada completa toma un valor propio con el que
# sella las filas que toca. Se usa una secuencia y no un reloj para que el
# sello sea único sin depender de la resolución ni de la monotonía del reloj.
SYNC_GENERATION_SEQUENCE = Sequence("product_sync_generation_seq")


class FullSyncReport(BaseModel):
    records_written: int = 0
    records_deleted: int = 0
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
    generation = session.scalar(select(SYNC_GENERATION_SEQUENCE.next_value()))

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
                    attribute_set_id=item.get("attribute_set_id"),
                    type_id=item.get("type_id"),
                    sync_generation=generation,
                )
                # Conjunto completo, no alta suelta: lo que el payload no trae
                # deja de estar asignado.
                set_product_categories(
                    session, tenant_id, item["sku"], item["category_ids"]
                )
                report.records_written += 1

        report.records_deleted += _sweep(session, tenant_id, store_id, generation)

    session.commit()
    return report


def _sweep(session: Session, tenant_id: int, store_id: int, generation: int) -> int:
    """Barre las filas de esa store view que esta pasada no selló.

    Se hace al terminar la pasada de la store view y no al final de todas,
    porque el conjunto que la pasada acaba de ver es la verdad completa de ESA
    tienda y de ninguna otra. El filtro por (tenant, store view) es lo que
    impide que barrer PY se lleve por delante BR, o el espejo de otro tenant.
    """
    result = session.execute(
        delete(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_id,
            ProductRecord.sync_generation != generation,
        )
    )
    return result.rowcount or 0
