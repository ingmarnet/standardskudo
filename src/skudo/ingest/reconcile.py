import hashlib

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.magento.client import MagentoClient
from skudo.mirror.models import ProductRecord


class DriftReport(BaseModel):
    store_view_magento_id: int
    magento_count: int
    mirror_count: int
    digest_matches: bool
    needs_full_sync: bool


def sku_digest(skus: list[str]) -> str:
    """Huella del conjunto de SKUs, independiente del orden.

    Se ordena antes de hashear para que Magento y el espejo puedan calcularla
    por separado y comparar sin coordinar paginación.
    """
    joined = "\n".join(sorted(skus))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def reconcile(
    session: Session, client: MagentoClient, tenant_id: int, store_view_magento_id: int
) -> DriftReport:
    remote = client.checksums(store_view_magento_id)

    local_skus = list(
        session.scalars(
            select(ProductRecord.sku).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
            )
        ).all()
    )

    digest_matches = sku_digest(local_skus) == remote["sku_digest"]
    count_matches = len(local_skus) == remote["product_count"]

    return DriftReport(
        store_view_magento_id=store_view_magento_id,
        magento_count=remote["product_count"],
        mirror_count=len(local_skus),
        digest_matches=digest_matches,
        # Cualquier discrepancia obliga a recarga completa: no se intenta
        # reparar por diferencias parciales, porque no sabemos qué más falta.
        needs_full_sync=not (digest_matches and count_matches),
    )
