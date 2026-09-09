from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Category, CategoryStoreState, ProductCategoryAssignment


class CategoryEffect(BaseModel):
    category_magento_id: int = 0
    store_view_magento_id: int = 0
    is_effective: bool
    reason: str


def derive_category_effect(
    assignment_path: list[int],
    root_category_id: int,
    is_active_in_store: bool,
    product_website_ids: list[int],
    store_website_id: int,
) -> CategoryEffect:
    """Decide si una asignación global tiene efecto en una store view concreta.

    Tres condiciones independientes, evaluadas en el orden en que un merchandiser
    las diagnosticaría. Se devuelve la primera que falla como `reason`, para que la
    UI pueda decir POR QUÉ el producto no aparece en vez de solo que no aparece.
    """
    if root_category_id not in assignment_path:
        return CategoryEffect(is_effective=False, reason="fuera_del_arbol_de_la_tienda")
    if not is_active_in_store:
        return CategoryEffect(is_effective=False, reason="categoria_inactiva_en_la_tienda")
    if store_website_id not in product_website_ids:
        return CategoryEffect(is_effective=False, reason="producto_fuera_del_website")
    return CategoryEffect(is_effective=True, reason="efectiva")


def upsert_category(
    session: Session, tenant_id: int, magento_id: int, path: list[int], name: str
) -> None:
    stmt = insert(Category).values(
        tenant_id=tenant_id, magento_id=magento_id, path=path, default_name=name
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "magento_id"],
            set_={"path": stmt.excluded.path, "default_name": stmt.excluded.default_name},
        )
    )
    session.flush()


def set_category_store_state(
    session: Session,
    tenant_id: int,
    magento_id: int,
    store_view_magento_id: int,
    is_active: bool,
    name: str,
) -> None:
    stmt = insert(CategoryStoreState).values(
        tenant_id=tenant_id, category_magento_id=magento_id,
        store_view_magento_id=store_view_magento_id, is_active=is_active, name=name,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "category_magento_id", "store_view_magento_id"],
            set_={"is_active": stmt.excluded.is_active, "name": stmt.excluded.name},
        )
    )
    session.flush()


def assign_product(
    session: Session, tenant_id: int, sku: str, category_magento_id: int
) -> None:
    stmt = insert(ProductCategoryAssignment).values(
        tenant_id=tenant_id, sku=sku, category_magento_id=category_magento_id
    )
    session.execute(
        stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "sku", "category_magento_id"]
        )
    )
    session.flush()
