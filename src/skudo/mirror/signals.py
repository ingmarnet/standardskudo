from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductRecord, ProductSignal

_UPDATABLE = (
    "units_sold", "revenue", "salable_qty", "physical_qty",
    "uses_msi", "margin", "search_demand",
)


def upsert_signals(
    session: Session, tenant_id: int, store_view_magento_id: int, rows: list[dict]
) -> int:
    if not rows:
        return 0

    values = [
        {
            "tenant_id": tenant_id,
            "store_view_magento_id": store_view_magento_id,
            "sku": row["sku"],
            "observed_at": func.clock_timestamp(),
            **{key: row.get(key) for key in _UPDATABLE},
        }
        for row in rows
    ]
    stmt = insert(ProductSignal).values(values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku", "store_view_magento_id"],
            set_={
                "observed_at": func.clock_timestamp(),
                **{key: getattr(stmt.excluded, key) for key in _UPDATABLE},
            },
        )
    )
    session.flush()
    return len(values)


def get_signal(
    session: Session, tenant_id: int, sku: str, store_view_magento_id: int
) -> ProductSignal | None:
    return session.scalar(
        select(ProductSignal).where(
            ProductSignal.tenant_id == tenant_id,
            ProductSignal.sku == sku,
            ProductSignal.store_view_magento_id == store_view_magento_id,
        )
    )


def delete_orphan_signals(
    session: Session, tenant_id: int, skus: list[str] | None = None
) -> int:
    """Borra las señales cuyo `(tenant_id, sku)` no tiene `product_record`.

    Misma forma y mismo razonamiento que
    `categories.delete_orphan_category_assignments`, sobre otra tabla: una
    señal de un SKU que el espejo ya no contiene describe un producto
    inexistente, y S1 PRIORIZA los hallazgos por señal. Un SKU fantasma con
    facturación no sería un hallazgo fabricado más: sería el PRIMERO que un
    humano ve.

    No contradice el "no hay barrido" que `sync_signals` declara. Aquello es
    sobre un SKU que deja de VENDER —su última medición sigue siendo el último
    hecho observado, fechado, y decidir cuándo caduca es del consumidor—;
    esto es sobre un SKU que deja de EXISTIR, y de él no hay nada que
    priorizar. El caso además está declarado como violación de contrato desde
    A1 (`signals_without_product_record` en el reporte de la pasada), así que
    la limpieza no lo esconde: `sync_signals` lo cuenta y lo nombra cuando
    escribe, mucho antes de que esto corra.

    `EXISTS` por CUALQUIER store view del tenant, igual que en las
    asignaciones: la señal es por tienda, pero un producto vivo en una tienda
    y retirado de la otra sigue siendo describible. Los dos filtros por tenant
    cargan el mismo peso que allá.
    """
    has_record = (
        select(ProductRecord.id)
        .where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.sku == ProductSignal.sku,
        )
        .exists()
    )
    where = [ProductSignal.tenant_id == tenant_id, ~has_record]
    if skus is not None:
        # Misma acotación y misma razón que en
        # `categories.delete_orphan_category_assignments`.
        if not skus:
            return 0
        where.append(ProductSignal.sku.in_(skus))
    result = session.execute(delete(ProductSignal).where(*where))
    session.flush()
    return result.rowcount or 0
