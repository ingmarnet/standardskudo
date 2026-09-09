import hashlib
import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
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


def content_hash(
    identity: ProductIdentity, effective: dict, store_view_magento_id: int
) -> str:
    """Hash estable del contenido relevante. Base del caché de la capa IA en S6.

    La store view entra en el material hasheado porque el veredicto sobre un
    mismo texto depende de la tienda: texto idéntico en PY y BR es exactamente
    el defecto de "nombre sin traducir". Sin ella, un caché con esta clave
    devolvería el veredicto de la tienda española para la portuguesa, justo en
    la población que más hay que juzgar.
    """
    material = json.dumps(
        {
            "store_view_magento_id": store_view_magento_id,
            "identity": identity.model_dump(),
            "attributes": effective,
        },
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
    *,
    attribute_set_id: int | None = None,
    type_id: str | None = None,
    sync_generation: int | None = None,
) -> None:
    """`attribute_set_id` y `type_id` son opcionales porque su ausencia es un
    hecho legítimo: la sonda puede no informarlos y NULL dice 'desconocido'.

    `sync_generation` solo lo pasa una pasada completa, que necesita sellar lo
    que tocó para poder barrer lo que no. Sin él, la fila conserva el sello que
    tuviera: una escritura incremental no puede hacer parecer viva a una fila
    que la próxima pasada completa no encuentre, ni al revés.
    """
    values: dict = {
        "tenant_id": tenant_id,
        "store_view_magento_id": store_view_magento_id,
        "sku": identity.sku,
        "mpn": identity.mpn,
        "model": identity.model,
        "gtin": identity.gtin,
        "variant_key": identity.variant_key,
        "attribute_set_id": attribute_set_id,
        "type_id": type_id,
        "attributes": effective,
        "scope_provenance": provenance,
        "content_hash": content_hash(identity, effective, store_view_magento_id),
        "magento_updated_at": magento_updated_at,
        # clock_timestamp() y no now(): now() es de alcance transaccional en
        # Postgres, así que dos upserts en la misma transacción escribirían la
        # misma hora y `mirrored_at` mentiría sobre la frescura del espejo.
        "mirrored_at": func.clock_timestamp(),
    }
    updatable = [
        "mpn", "model", "gtin", "variant_key", "attribute_set_id", "type_id",
        "attributes", "scope_provenance", "content_hash", "magento_updated_at",
        "mirrored_at",
    ]
    if sync_generation is not None:
        values["sync_generation"] = sync_generation
        updatable.append("sync_generation")

    stmt = insert(ProductRecord).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku", "store_view_magento_id"],
            set_={column: getattr(stmt.excluded, column) for column in updatable},
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
