from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Sequence, delete, select
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.categories import set_product_categories
from skudo.mirror.models import ProductRecord
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record
from skudo.mirror.topology import sync_topology

# Secuencia de la base: cada pasada completa toma un valor propio con el que
# sella las filas que toca. Se usa una secuencia y no un reloj para que el
# sello sea único sin depender de la resolución ni de la monotonía del reloj.
SYNC_GENERATION_SEQUENCE = Sequence("product_sync_generation_seq")


# Cuántos SKUs con fecha ilegible se nombran en el reporte. Un conteo solo dice
# que hay filas afectadas; una muestra acotada dice cuáles mirar.
UNTIMESTAMPED_SAMPLE_SIZE = 20


class FullSyncReport(BaseModel):
    records_written: int = 0
    records_deleted: int = 0
    pages_fetched: int = 0
    records_without_timestamp: int = 0
    skus_without_timestamp: list[str] = []


def parse_magento_datetime(raw: str | None) -> datetime | None:
    """Magento devuelve 'YYYY-MM-DD HH:MM:SS' en UTC, sin zona explícita.

    Política explícita para lo que no se puede interpretar —la cadena vacía y el
    '0000-00-00 00:00:00' que MySQL admite y los catálogos heredados contienen—:
    es DESCONOCIDO, y se devuelve None. Ni una excepción, que abortaría la
    página entera y en `delta_sync` bloquearía el avance del watermark y con él
    toda sincronización posterior; ni una fecha de relleno, que sería un dato
    falso con aspecto confiable. Quien llama reporta el caso.
    """
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate or candidate.startswith("0000-00-00"):
        return None
    try:
        return datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def note_unreadable_timestamp(report, sku: str) -> None:
    """Anota en el reporte que un SKU llegó sin fecha interpretable."""
    report.records_without_timestamp += 1
    if len(report.skus_without_timestamp) < UNTIMESTAMPED_SAMPLE_SIZE:
        report.skus_without_timestamp.append(sku)


def full_sync(
    session: Session,
    source: TenantSource,
    store_view_ids: list[int],
) -> FullSyncReport:
    """Carga completa del catálogo, una pasada por store view.

    Se recorre por store view porque los valores de override viven en ese scope:
    una sola pasada global no permitiría saber qué heredó cada tienda.

    Recibe un `TenantSource` y no `(client, tenant_id)` para que el catálogo que
    se lee y el espejo en el que se escribe no puedan ser de tenants distintos.
    """
    tenant_id = source.tenant_id
    client = source.client
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
                magento_updated_at = parse_magento_datetime(item.get("updated_at"))
                if magento_updated_at is None:
                    note_unreadable_timestamp(report, item["sku"])
                upsert_record(
                    session,
                    tenant_id,
                    store_id,
                    identity,
                    effective,
                    provenance,
                    magento_updated_at,
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
