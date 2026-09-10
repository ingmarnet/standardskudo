from pydantic import BaseModel
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.categories import set_category_store_state, upsert_category


class CategorySyncReport(BaseModel):
    pages_fetched: int = 0
    categories_written: int = 0


def sync_categories(
    session: Session,
    source: TenantSource,
    store_view_ids: list[int],
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

    No hay barrido de categorías revocadas globalmente en esta tarea: la
    revocación de asignaciones producto-categoría vía `delta_sync` es Task A5,
    fuera de alcance aquí.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = CategorySyncReport()

    for page in client.iter_categories():
        report.pages_fetched += 1
        for item in page["items"]:
            upsert_category(
                session,
                tenant_id,
                item["category_id"],
                item["path"],
                item["default_name"],
            )
            for state in item["store_states"]:
                set_category_store_state(
                    session,
                    tenant_id,
                    item["category_id"],
                    state["store_id"],
                    state["is_active"],
                    state["name"],
                )
            report.categories_written += 1

    session.commit()
    return report
