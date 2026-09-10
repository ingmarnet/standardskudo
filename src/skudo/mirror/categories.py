from pydantic import BaseModel
from sqlalchemy import delete, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Category, CategoryStoreState, ProductCategoryAssignment


class CategoryEffect(BaseModel):
    """Solo el veredicto y su motivo.

    No lleva `category_magento_id` ni `store_view_magento_id`: quien pregunta ya
    sabe por qué par preguntó, y un campo de identidad con default 0 que nadie
    rellena solo sirve para que el primer consumidor lea un cero creíble.

    `is_effective` es `bool | None` y no un `bool` liso: `None` significa "no
    evaluado", no "falló". Todo `product_record` espejado antes de la migración
    `0011` tiene `website_ids = None` porque ningún sync todavía lo llenó — un
    dato AUSENTE, no un dato que diga "el producto no está en ningún website".
    Confundir esos dos es exactamente el riesgo que la sección 1 del spec nombra
    como el mayor de todo el sistema. `None` aquí se alinea con la vocabulario
    de cobertura de la sección 6.4 (`evaluado` / `no_evaluado` / `no_aplica`):
    un control que no se pudo evaluar queda pendiente, nunca aprobado ni
    reprobado.
    """

    is_effective: bool | None
    reason: str


def derive_category_effect(
    assignment_path: list[int],
    root_category_id: int,
    is_active_in_store: bool,
    product_website_ids: list[int] | None,
    store_website_id: int,
) -> CategoryEffect:
    """Decide si una asignación global tiene efecto en una store view concreta.

    Tres condiciones independientes, evaluadas en el orden en que un merchandiser
    las diagnosticaría. Se devuelve la primera que falla como `reason`, para que la
    UI pueda decir POR QUÉ el producto no aparece en vez de solo que no aparece.

    `product_website_ids=None` es DESCONOCIDO, no "fuera de todo website": las
    dos primeras condiciones (árbol y actividad por tienda) no dependen del
    website y sí pueden fallar con certeza aunque el website sea desconocido, así
    que se evalúan primero y solo la tercera condición se topa con lo
    desconocido. `product_website_ids=[]` en cambio es CONOCIDO — el producto de
    verdad no está asignado a ningún website, que es un defecto real (invisible
    en todas partes) — y sigue devolviendo `producto_fuera_del_website`, igual
    que antes de este cambio.
    """
    if root_category_id not in assignment_path:
        return CategoryEffect(is_effective=False, reason="fuera_del_arbol_de_la_tienda")
    if not is_active_in_store:
        return CategoryEffect(is_effective=False, reason="categoria_inactiva_en_la_tienda")
    if product_website_ids is None:
        return CategoryEffect(is_effective=None, reason="website_desconocido")
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


def set_products_categories(
    session: Session, tenant_id: int, assignments: dict[str, list[int]]
) -> None:
    """Reemplaza el conjunto completo de categorías de VARIOS productos.

    Es una operación de conjunto y no de alta suelta a propósito: dar de alta
    sin contraparte deja al producto asignado a una categoría de la que ya
    salió, y ni una recarga completa lo repara. El payload de Magento es la
    verdad completa del conjunto, así que lo que no trae se revoca.

    El alcance del borrado es (tenant, sku) porque en el core de Magento la
    asignación no tiene store view: es global.

    Por qué en lote (H3, medido): de a un SKU eran dos sentencias por
    producto, y el coste dominante era la compilación del SQL en SQLAlchemy,
    no la base. Una página de 500 productos pasa de 1.000 sentencias a dos.
    La semántica es la misma, escrita como conjunto: se borra lo que está en
    los SKUs de este lote y NO está entre los pares deseados, y se insertan
    los pares deseados. Un SKU con lista vacía queda cubierto por el primer
    filtro y pierde todas sus asignaciones, igual que antes.
    """
    if not assignments:
        return

    skus = list(assignments)
    wanted_pairs = sorted(
        {(sku, category_id) for sku, ids in assignments.items() for category_id in ids}
    )

    stale = [
        ProductCategoryAssignment.tenant_id == tenant_id,
        ProductCategoryAssignment.sku.in_(skus),
    ]
    if wanted_pairs:
        # Ningún SKU del lote conserva un par que no esté en la lista deseada.
        # Sin pares deseados el filtro se omite —un `NOT IN ()` vacío no dice
        # nada— y el borrado se lleva todas las asignaciones de esos SKUs, que
        # es exactamente lo que "ninguno tiene categorías" significa.
        stale.append(
            tuple_(
                ProductCategoryAssignment.sku,
                ProductCategoryAssignment.category_magento_id,
            ).notin_(wanted_pairs)
        )
    session.execute(delete(ProductCategoryAssignment).where(*stale))

    if wanted_pairs:
        stmt = insert(ProductCategoryAssignment).values(
            [
                {"tenant_id": tenant_id, "sku": sku, "category_magento_id": category_id}
                for sku, category_id in wanted_pairs
            ]
        )
        session.execute(
            stmt.on_conflict_do_nothing(
                index_elements=["tenant_id", "sku", "category_magento_id"]
            )
        )
    session.flush()
