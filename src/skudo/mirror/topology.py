from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.magento.environment import EnvironmentProfile
from skudo.mirror.models import EnvironmentSnapshot, StoreGroup, StoreView, Website


def sync_topology(session: Session, tenant_id: int, profile: EnvironmentProfile) -> None:
    """Refleja websites, grupos y store views. Idempotente por (tenant, magento_id)."""
    session.add(
        EnvironmentSnapshot(
            tenant_id=tenant_id,
            edition=profile.edition,
            version=profile.version,
            product_entity_key=profile.product_entity_key,
            staging_enabled=profile.staging_enabled,
            msi_enabled=profile.msi_enabled,
            payload=profile.model_dump(),
        )
    )

    _upsert(
        session,
        Website,
        ["code", "name"],
        [
            {"tenant_id": tenant_id, "magento_id": w.id, "code": w.code, "name": w.name}
            for w in profile.websites
        ],
    )
    _upsert(
        session,
        StoreGroup,
        ["website_magento_id", "code", "name", "root_category_id"],
        [
            {
                "tenant_id": tenant_id,
                "magento_id": g.id,
                "website_magento_id": g.website_id,
                "code": g.code,
                "name": g.name,
                "root_category_id": g.root_category_id,
            }
            for g in profile.store_groups
        ],
    )
    _upsert(
        session,
        StoreView,
        ["group_magento_id", "code", "name", "is_active", "locale", "currency"],
        [
            {
                "tenant_id": tenant_id,
                "magento_id": v.id,
                "group_magento_id": v.group_id,
                "code": v.code,
                "name": v.name,
                "is_active": v.is_active,
                "locale": v.locale,
                "currency": v.currency,
            }
            for v in profile.store_views
        ],
    )
    session.flush()


def _upsert(session: Session, model, update_columns: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    stmt = insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "magento_id"],
        set_={col: getattr(stmt.excluded, col) for col in update_columns},
    )
    session.execute(stmt)


def root_category_id(session: Session, tenant_id: int, store_view_id: int) -> int:
    """La store view no conoce su root category: la hereda de su grupo."""
    group_magento_id = session.scalar(
        select(StoreView.group_magento_id).where(
            StoreView.tenant_id == tenant_id, StoreView.magento_id == store_view_id
        )
    )
    if group_magento_id is None:
        raise KeyError(f"store view desconocida: {store_view_id}")

    root = session.scalar(
        select(StoreGroup.root_category_id).where(
            StoreGroup.tenant_id == tenant_id, StoreGroup.magento_id == group_magento_id
        )
    )
    if root is None:
        raise KeyError(f"store group desconocido: {group_magento_id}")
    return root
