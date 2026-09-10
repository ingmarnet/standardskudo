from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from skudo.ingest.apply import apply_items
from skudo.ingest.source import TenantSource

# `IncompletePassSweep` y el sello de pasada viven en `ingest.sweep` desde M3,
# que generalizó la maquinaria de H3 a las pasadas de atributos y categorías.
# Una sola excepción para los tres barridos a propósito: dos tipos distintos
# para el mismo modo de fallo invitarían a tratarlos como problemas distintos.
from skudo.ingest.sweep import IncompletePassSweep, next_generation
from skudo.mirror.attributes import declared_scopes
from skudo.mirror.categories import delete_orphan_category_assignments
from skudo.mirror.models import FullSyncCheckpoint, ProductRecord
from skudo.mirror.signals import delete_orphan_signals
from skudo.mirror.topology import sync_topology

# Tamaño de página del recorrido por cursor, y por tanto de la transacción:
# se confirma UNA vez por página. 500 es el default del módulo
# (`ProductReader::getPage`) y deja una transacción de ~500 upserts más sus
# categorías, que Postgres cierra en decenas de milisegundos.
FULL_SYNC_PAGE_SIZE = 500


class FullSyncReport(BaseModel):
    # Sello de la pasada. Una reanudación reporta el MISMO valor que la
    # pasada que continúa: es lo que permite verificar desde fuera que no
    # empezó una generación nueva.
    generation: int = 0
    resumed: bool = False
    records_written: int = 0
    records_deleted: int = 0
    # M3: las asignaciones producto-categoría que quedaron sin producto. En la
    # pasada de escala de H3 fueron 457.762, una por cada `ProductRecord` que
    # el barrido soltó, porque `_sweep` borraba esa tabla y ninguna más.
    category_assignments_deleted: int = 0
    # Señales comerciales que quedaron sin producto, misma historia.
    signals_deleted: int = 0
    pages_fetched: int = 0
    # Store views cuya pasada terminó y fue barrida EN ESTA invocación.
    store_views_completed: list[int] = []
    # Store views que esta invocación no volvió a recorrer porque la pasada
    # de esta misma generación ya las había terminado y barrido.
    store_views_already_done: list[int] = []
    records_without_timestamp: int = 0
    skus_without_timestamp: list[str] = []


def _checkpoint(
    session: Session, tenant_id: int, store_id: int
) -> FullSyncCheckpoint | None:
    return session.scalar(
        select(FullSyncCheckpoint).where(
            FullSyncCheckpoint.tenant_id == tenant_id,
            FullSyncCheckpoint.store_view_magento_id == store_id,
        )
    )


def _open_generation(session: Session, tenant_id: int) -> int | None:
    """La generación de una pasada que quedó a medias, si hay una.

    "A medias" es NO (pasada completa Y barrida): una store view cuya última
    página se aplicó pero cuyo barrido no llegó a correr también está a
    medias, y hay que volver a ella con su MISMA generación para que el
    barrido siga siendo posible. Si tomara una generación nueva, el sello de
    todo lo que esa pasada ya escribió quedaría viejo y el barrido lo borraría.

    Se toma el máximo por si dos store views quedaran abiertas en
    generaciones distintas (una pasada anterior interrumpida y otra
    reiniciada con `--restart`): la más reciente es la que se continúa, y la
    vieja se cierra sola cuando su store view vuelva a recorrerse.
    """
    return session.scalar(
        select(func.max(FullSyncCheckpoint.generation)).where(
            FullSyncCheckpoint.tenant_id == tenant_id,
            ~(FullSyncCheckpoint.pass_complete & FullSyncCheckpoint.swept),
        )
    )


def _start_pass(
    session: Session, tenant_id: int, store_id: int, generation: int
) -> FullSyncCheckpoint:
    """Deja el checkpoint de esa store view listo para esta generación.

    Si ya existe uno de la MISMA generación sin terminar, se continúa desde su
    cursor. Si es de otra generación, se reinicia: el cursor de una pasada
    anterior no significa nada para esta.
    """
    checkpoint = _checkpoint(session, tenant_id, store_id)
    if checkpoint is None:
        checkpoint = FullSyncCheckpoint(
            tenant_id=tenant_id,
            store_view_magento_id=store_id,
            generation=generation,
            next_cursor=None,
            pages_done=0,
            records_written=0,
            pass_complete=False,
            swept=False,
        )
        session.add(checkpoint)
    elif checkpoint.generation != generation:
        checkpoint.generation = generation
        checkpoint.next_cursor = None
        checkpoint.pages_done = 0
        checkpoint.records_written = 0
        checkpoint.pass_complete = False
        checkpoint.swept = False
    checkpoint.updated_at = datetime.now(UTC)
    session.flush()
    return checkpoint


def full_sync(
    session: Session,
    source: TenantSource,
    store_view_ids: list[int],
    *,
    page_size: int = FULL_SYNC_PAGE_SIZE,
    restart: bool = False,
) -> FullSyncReport:
    """Carga completa del catálogo, una pasada por store view, REANUDABLE.

    Se recorre por store view porque los valores de override viven en ese
    scope: una sola pasada global no permitiría saber qué heredó cada tienda.

    Recibe un `TenantSource` y no `(client, tenant_id)` para que el catálogo que
    se lee y el espejo en el que se escribe no puedan ser de tenants distintos.

    ORDEN: `sync_attributes` debería correr ANTES que esta pasada. El mapa de
    scopes declarados que `resolve_scope` necesita para distinguir un override
    de website de uno de tienda sale de la tabla `attribute`; si está vacía, la
    procedencia de cada override queda en DESCONOCIDO —que es la verdad, no un
    fallo silencioso, y el criterio de aceptación `procedencia_de_scope` lo
    reprueba—. Se lee UNA vez por pasada, no por producto.

    COMMIT POR PÁGINA (H3). Antes había UN `commit()` al final: para el
    catálogo piloto, ~457.000 upserts más un par delete+insert de categorías
    por producto en una sola transacción de Postgres, sin lotes y sin
    reanudación. Un fallo a la tercera hora no dejaba nada. Ahora cada página
    se confirma con su punto de reanudación en la MISMA transacción
    (`full_sync_checkpoint`), así que los datos y el cursor avanzan juntos o
    no avanzan.

    El modo de fallo que eso INTRODUCE, y cómo se cierra: una pasada
    interrumpida deja el espejo parcialmente actualizado. Eso es aceptable —lo
    que había sigue siendo el último hecho observado— pero el BARRIDO deja de
    serlo: barrer "lo que esta pasada no selló" sobre una pasada que vio tres
    páginas de cuatrocientas borraría casi todo el catálogo. La precondición
    del barrido es el sello persistido `pass_complete`, que `_sweep` lee de la
    base, así que un barrido sobre una pasada a medias no es expresable.

    REANUDACIÓN: por defecto se continúa la pasada abierta, con su MISMA
    generación. Con `restart=True` se toma una generación nueva y los
    checkpoints se reinician.

    HUÉRFANOS (M3). El barrido borra `ProductRecord` y nada más, así que en la
    pasada de escala de H3 dejó 457.762 asignaciones producto-categoría sin
    producto. Al final del recorrido de todas las store views se limpian por
    referencia (`delete_orphan_category_assignments`), no por sello: la
    asignación es global y el registro es por store view, así que un SKU sigue
    siendo legítimo mientras alguna tienda lo tenga.
    """
    tenant_id = source.tenant_id
    client = source.client
    profile = client.environment()
    sync_topology(session, tenant_id, profile)

    report = FullSyncReport()
    open_generation = None if restart else _open_generation(session, tenant_id)
    if open_generation is None:
        generation = next_generation(session)
    else:
        generation = open_generation
        report.resumed = True
    report.generation = generation
    # La topología y la generación se confirman antes de la primera página:
    # sin esto, la primera página arrastraría el snapshot de entorno en su
    # transacción y un fallo ahí perdería también la sonda.
    session.commit()

    scopes = declared_scopes(session, tenant_id)

    for store_id in store_view_ids:
        checkpoint = _checkpoint(session, tenant_id, store_id)
        if (
            checkpoint is not None
            and checkpoint.generation == generation
            and checkpoint.pass_complete
            and checkpoint.swept
        ):
            # Ya terminada y barrida en ESTA generación: volver a recorrerla
            # sería releer el catálogo entero para reescribir lo mismo.
            report.store_views_already_done.append(store_id)
            continue

        checkpoint = _start_pass(session, tenant_id, store_id, generation)
        cursor = checkpoint.next_cursor
        session.commit()

        while not checkpoint.pass_complete:
            page = client.products_page(store_id, limit=page_size, cursor=cursor)
            report.pages_fetched += 1
            applied = apply_items(
                session,
                tenant_id,
                store_id,
                page["items"],
                scopes,
                report,
                sync_generation=generation,
            )
            report.records_written += applied

            cursor = page.get("next_cursor")
            checkpoint.next_cursor = cursor
            checkpoint.pages_done += 1
            checkpoint.records_written += applied
            # El sello de "vio la última página" se escribe en la MISMA
            # transacción que la última página, no antes: si el commit no
            # llega, la pasada sigue estando a medias y el barrido sigue
            # prohibido.
            checkpoint.pass_complete = cursor is None
            checkpoint.updated_at = datetime.now(UTC)
            session.commit()

        report.records_deleted += _sweep(session, tenant_id, store_id, generation)
        report.store_views_completed.append(store_id)
        session.commit()

    # M3. Después del recorrido de TODAS las store views y no dentro del bucle:
    # el registro de producto es por store view y la asignación es global, así
    # que un SKU que el origen retiró de PY pero sigue ofreciendo en BR no es
    # huérfano hasta que ninguna tienda lo tenga. Correrlo por tienda dejaría
    # sin categorías, a mitad de la pasada, a productos vivos en la otra.
    #
    # Y después del bucle en el sentido fuerte: una interrupción nunca llega
    # acá, así que una pasada a medias no limpia nada —el fallo benigno—.
    report.category_assignments_deleted = delete_orphan_category_assignments(
        session, tenant_id
    )
    # Y la señal comercial del producto que ya no está, por la misma razón y
    # con la misma forma: S1 prioriza POR señal, así que un SKU fantasma con
    # facturación sería el primer hallazgo que un humano ve.
    report.signals_deleted = delete_orphan_signals(session, tenant_id)
    session.commit()

    return report


def _sweep(session: Session, tenant_id: int, store_id: int, generation: int) -> int:
    """Barre las filas de esa store view que esta pasada no selló.

    Se hace al terminar la pasada de la store view y no al final de todas,
    porque el conjunto que la pasada acaba de ver es la verdad completa de ESA
    tienda y de ninguna otra. El filtro por (tenant, store view) es lo que
    impide que barrer PY se lleve por delante BR, o el espejo de otro tenant.

    La precondición se lee de la BASE y no de una variable del llamador: el
    checkpoint de esa store view tiene que existir, ser de ESTA generación y
    llevar `pass_complete`. Con commit por página, un barrido sobre una pasada
    a medias borraría todo lo que la pasada no alcanzó a recorrer —que es la
    mayor parte del catálogo—, así que la comprobación vive acá adentro y no
    en el llamador: ningún camino puede omitirla.
    """
    checkpoint = _checkpoint(session, tenant_id, store_id)
    if checkpoint is None or checkpoint.generation != generation:
        raise IncompletePassSweep(
            f"no hay checkpoint de la generación {generation} para la store view "
            f"{store_id} del tenant {tenant_id}: no se puede barrer una pasada "
            "que esta generación no registró. Barrer sin ese sello borraría "
            "todas las filas que la pasada no alcanzó a sellar."
        )
    if not checkpoint.pass_complete:
        raise IncompletePassSweep(
            f"la pasada de la store view {store_id} (generación {generation}, "
            f"{checkpoint.pages_done} página(s) aplicada(s)) NO llegó a la última "
            "página: barrer ahora borraría todo lo que falta por recorrer. Se "
            "aborta; la reanudación continúa desde el cursor guardado y el "
            "barrido corre cuando la pasada termine."
        )

    result = session.execute(
        delete(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_id,
            ProductRecord.sync_generation != generation,
        )
    )
    checkpoint.swept = True
    checkpoint.updated_at = datetime.now(UTC)
    session.flush()
    return result.rowcount or 0
