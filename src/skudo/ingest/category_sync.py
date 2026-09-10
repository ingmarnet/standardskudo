from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.ingest.sweep import (
    PASS_CATEGORIES,
    guard_mass_sweep,
    next_generation,
    note_page,
    require_complete_pass,
    start_pass,
)
from skudo.mirror.categories import set_category_store_state, upsert_category
from skudo.mirror.models import Category, CategoryStoreState


class CategorySyncReport(BaseModel):
    # Sello de esta pasada, igual que en `AttributeSyncReport`.
    generation: int = 0
    pages_fetched: int = 0
    categories_written: int = 0
    # M3: lo que el origen dejó de ofrecer y esta pasada barrió.
    categories_deleted: int = 0
    category_store_states_deleted: int = 0


def sync_categories(
    session: Session,
    source: TenantSource,
    store_view_ids: list[int],
    *,
    sweep_anyway: bool = False,
) -> CategorySyncReport:
    """Vuelca categorías (path + estado por store view) en el espejo.

    Sin esto, `Category.path` y `CategoryStoreState.is_active` nunca se
    escribían y `derive_category_effect` —pura y ya probada en Python— era
    inalcanzable desde datos de espejo real: el endpoint de Task A3 existía,
    pero nada del lado Python lo leía.

    `store_view_ids` no filtra qué se guarda: el endpoint siempre emite una
    entrada de `store_states` por CADA store view de la instancia (Task A3), y
    aquí se vuelcan todas. El parámetro se recibe por simetría con
    `full_sync`/`delta_sync` y porque una futura ronda de esta tarea podría
    necesitar acotar la llamada; hoy no cambia qué se escribe.

    Recibe un `TenantSource` y no `(client, tenant_id)` sueltos, por la misma
    razón que `full_sync`/`delta_sync`/`sync_attributes`: para que el catálogo
    que se lee y el tenant en el que se escribe no puedan desemparejarse.

    BARRIDO (M3). Hasta este cierre esta función documentaba "no hay barrido"
    como fuera de alcance: una categoría que el origen borraba —o el estado
    por tienda de una store view retirada— seguía pareciendo viva en el espejo
    para siempre, y los detectores del eje 2 de S1 (salud del árbol,
    accesibilidad efectiva) leen esas dos tablas. Mismo mecanismo, misma
    puerta y misma razón para no reanudar que en `sync_attributes`.

    Lo que este barrido NO hace, y por qué: no toca
    `product_category_assignment`. Esa tabla se limpia por REFERENCIA
    (`delete_orphan_category_assignments`, llamada por `full_sync` y por el
    camino de borrado de `delta_sync`), porque una asignación es huérfana por
    no tener producto, no por no llevar el sello de la última pasada de
    categorías. Barrerla desde acá borraría las asignaciones de productos
    vivos cuya categoría sigue existiendo.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = CategorySyncReport()

    generation = next_generation(session)
    report.generation = generation
    pass_row = start_pass(session, tenant_id, PASS_CATEGORIES, generation)
    session.commit()

    for page in client.iter_categories():
        report.pages_fetched += 1
        for item in page["items"]:
            upsert_category(
                session,
                tenant_id,
                item["category_id"],
                item["path"],
                item["default_name"],
                sync_generation=generation,
            )
            for state in item["store_states"]:
                set_category_store_state(
                    session,
                    tenant_id,
                    item["category_id"],
                    state["store_id"],
                    state["is_active"],
                    state["name"],
                    sync_generation=generation,
                )
            report.categories_written += 1

        # En la MISMA transacción que la página, igual que en `full_sync` y en
        # `sync_attributes`: si el commit no llega, la pasada sigue a medias y
        # el barrido sigue prohibido.
        note_page(
            session, pass_row, len(page["items"]), is_last=page.get("next_cursor") is None
        )
        session.commit()

    (
        report.categories_deleted,
        report.category_store_states_deleted,
    ) = _sweep_categories(session, tenant_id, generation, sweep_anyway=sweep_anyway)
    session.commit()
    return report


def _sweep_categories(
    session: Session, tenant_id: int, generation: int, *, sweep_anyway: bool = False
) -> tuple[int, int]:
    """Borra categorías y estados por tienda que esta pasada no selló.

    La precondición se lee de la BASE (`require_complete_pass`): un barrido
    sobre una pasada a medias borraría el árbol que falta por recorrer, y no
    es expresable.

    El estado por tienda se filtra por su PROPIO sello y no por el de su
    categoría: una store view retirada de la instancia deja la categoría viva
    y su estado huérfano, y `derive_category_effect` leería un `is_active` de
    una tienda que ya no existe. Con el sello de la categoría como criterio,
    ese caso no se vería.
    """
    pass_row = require_complete_pass(session, tenant_id, PASS_CATEGORIES, generation)

    # La válvula, sobre las dos tablas selladas y por separado, igual que en la
    # pasada de atributos: un árbol entero que desaparece y un estado por
    # tienda que desaparece son dos hechos distintos.
    for model, what in (
        (Category, "category"),
        (CategoryStoreState, "category_store_state"),
    ):
        total, doomed = session.execute(
            select(
                func.count(),
                func.count().filter(model.sync_generation != generation),
            ).where(model.tenant_id == tenant_id)
        ).one()
        guard_mass_sweep(
            session, what=what, total=total, to_delete=doomed, override=sweep_anyway
        )

    states_deleted = session.execute(
        delete(CategoryStoreState).where(
            CategoryStoreState.tenant_id == tenant_id,
            CategoryStoreState.sync_generation != generation,
        )
    )
    categories_deleted = session.execute(
        delete(Category).where(
            Category.tenant_id == tenant_id,
            Category.sync_generation != generation,
        )
    )

    pass_row.swept = True
    session.flush()
    return categories_deleted.rowcount or 0, states_deleted.rowcount or 0
