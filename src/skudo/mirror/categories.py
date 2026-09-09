from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Category, CategoryStoreState, ProductCategoryAssignment


class CategoryEffect(BaseModel):
    """Solo el veredicto y su motivo.

    No lleva `category_magento_id` ni `store_view_magento_id`: quien pregunta ya
    sabe por qué par preguntó, y un campo de identidad con default 0 que nadie
    rellena solo sirve para que el primer consumidor lea un cero creíble.
    """

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


def set_product_categories(
    session: Session, tenant_id: int, sku: str, category_magento_ids: list[int]
) -> None:
    """Reemplaza el conjunto completo de categorías de un producto.

    Es una operación de conjunto y no de alta suelta a propósito: dar de alta
    sin contraparte deja al producto asignado a una categoría de la que ya
    salió, y ni una recarga completa lo repara. El payload de Magento es la
    verdad completa del conjunto, así que lo que no trae se revoca.

    El alcance del borrado es (tenant, sku) porque en el core de Magento la
    asignación no tiene store view: es global.
    """
    wanted = sorted(set(category_magento_ids))

    stale = [
        ProductCategoryAssignment.tenant_id == tenant_id,
        ProductCategoryAssignment.sku == sku,
    ]
    if wanted:
        stale.append(ProductCategoryAssignment.category_magento_id.notin_(wanted))
    session.execute(delete(ProductCategoryAssignment).where(*stale))

    if wanted:
        stmt = insert(ProductCategoryAssignment).values(
            [
                {"tenant_id": tenant_id, "sku": sku, "category_magento_id": category_id}
                for category_id in wanted
            ]
        )
        session.execute(
            stmt.on_conflict_do_nothing(
                index_elements=["tenant_id", "sku", "category_magento_id"]
            )
        )
    session.flush()
