import hashlib
import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductRecord


class ProductIdentity(BaseModel):
    """Identidad desglosada. Todo texto: normalizar a número destruye la identidad."""

    sku: str
    mpn: str | None = None
    model: str | None = None
    gtin: str | None = None
    variant_key: str | None = None


def resolve_scope(
    global_values: dict[str, str], store_values: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Resuelve el valor efectivo y registra de dónde vino.

    La presencia de una clave en `store_values` decide la procedencia, no su
    contenido: un valor vacío puesto en la store view es un valor de store view,
    y colapsarlo con la herencia global ocultaría un defecto de traducción.
    """
    effective: dict[str, str] = dict(global_values)
    provenance: dict[str, str] = {code: "global" for code in global_values}

    for code, value in store_values.items():
        effective[code] = value
        provenance[code] = "store"

    return effective, provenance


def content_hash(identity: ProductIdentity, effective: dict) -> str:
    """Hash estable del contenido relevante. Base del caché de la capa IA en S6."""
    material = json.dumps(
        {"identity": identity.model_dump(), "attributes": effective},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def upsert_record(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    identity: ProductIdentity,
    effective: dict,
    provenance: dict,
    magento_updated_at: datetime,
) -> None:
    stmt = insert(ProductRecord).values(
        tenant_id=tenant_id,
        store_view_magento_id=store_view_magento_id,
        sku=identity.sku,
        mpn=identity.mpn,
        model=identity.model,
        gtin=identity.gtin,
        variant_key=identity.variant_key,
        attributes=effective,
        scope_provenance=provenance,
        content_hash=content_hash(identity, effective),
        magento_updated_at=magento_updated_at,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku", "store_view_magento_id"],
            set_={
                "mpn": stmt.excluded.mpn,
                "model": stmt.excluded.model,
                "gtin": stmt.excluded.gtin,
                "variant_key": stmt.excluded.variant_key,
                "attributes": stmt.excluded.attributes,
                "scope_provenance": stmt.excluded.scope_provenance,
                "content_hash": stmt.excluded.content_hash,
                "magento_updated_at": stmt.excluded.magento_updated_at,
            },
        )
    )
    session.flush()


def get_record(
    session: Session, tenant_id: int, sku: str, store_view_magento_id: int
) -> ProductRecord | None:
    return session.scalar(
        select(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.sku == sku,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
