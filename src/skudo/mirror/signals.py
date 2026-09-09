from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductSignal

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
