from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.ingest.full_sync import note_unreadable_timestamp, parse_magento_datetime
from skudo.ingest.source import TenantSource
from skudo.mirror.categories import set_product_categories
from skudo.mirror.models import ProductRecord, SyncWatermark
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record

# Margen de solape, en segundos, al pedir las activaciones de versión
# programada. `created_in` lo pone el reloj de la base de Magento; el instante
# de "última lectura" lo pone el nuestro. Una deriva entre los dos en el
# sentido malo se traga una activación PARA SIEMPRE (nada la vuelve a
# ofrecer: no hay evento, y `reconcile()` compara conjuntos de SKU, no
# valores). Con el solape, la deriva causa una reentrega —inofensiva, porque
# todo el camino de refresco es upsert por (tenant, sku, store view)— en vez
# de una pérdida silenciosa. Cinco minutos cubre con holgura la deriva de un
# NTP normal sin ampliar la ventana a algo caro.
DELTA_ACTIVATION_OVERLAP_SECONDS = 300


class DeltaSyncReport(BaseModel):
    changes_seen: int = 0
    records_updated: int = 0
    records_deleted: int = 0
    watermark: int = 0
    records_without_timestamp: int = 0
    skus_without_timestamp: list[str] = []


def _read_watermark(session: Session, tenant_id: int) -> int:
    value = session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == tenant_id)
    )
    return value or 0


def _read_last_delta_read_at(session: Session, tenant_id: int) -> datetime | None:
    return session.scalar(
        select(SyncWatermark.last_delta_read_at).where(
            SyncWatermark.tenant_id == tenant_id
        )
    )


def _since_timestamp(last_read_at: datetime | None) -> int | None:
    """Unix seconds desde los que pedir las activaciones de versión, o None.

    None en la PRIMERA lectura de un tenant: no hay ventana anterior, y mandar
    0 haría que `created_in > 0` devolviera la versión activa de todo el
    catálogo como si se acabara de activar. La primera pasada solo deja
    anotado el instante desde el que la siguiente puede preguntar.
    """
    if last_read_at is None:
        return None
    return int(last_read_at.timestamp()) - DELTA_ACTIVATION_OVERLAP_SECONDS


def _write_watermark(
    session: Session,
    tenant_id: int,
    change_id: int,
    delta_read_at: datetime | None = None,
) -> None:
    """`updated_at` se escribe explícitamente en los `values` y en el `set_`.

    Un `onupdate=` del modelo NO se aplica a un `insert().on_conflict_do_update()`
    de Core, así que declararlo allí y confiar en él dejaba la columna congelada
    en la hora del primer insert. Y es `clock_timestamp()` y no `now()` porque
    `now()` es de alcance transaccional en Postgres: dos avances de watermark en
    la misma transacción registrarían la misma hora.

    `delta_read_at` es OPCIONAL y solo se escribe cuando se pasa: el watermark
    de cambios avanza página a página, pero el de tiempo solo al terminar el
    recorrido entero sin error. Escribirlo por página adelantaría la ventana de
    activaciones sobre un recorrido que todavía puede fallar a la mitad, y esa
    parte de la ventana no la vuelve a ofrecer nadie.
    """
    values: dict = {
        "tenant_id": tenant_id,
        "last_change_id": change_id,
        "updated_at": func.clock_timestamp(),
    }
    if delta_read_at is not None:
        values["last_delta_read_at"] = delta_read_at

    stmt = insert(SyncWatermark).values(**values)
    set_: dict = {
        "last_change_id": stmt.excluded.last_change_id,
        "updated_at": func.clock_timestamp(),
    }
    if delta_read_at is not None:
        set_["last_delta_read_at"] = stmt.excluded.last_delta_read_at

    session.execute(
        stmt.on_conflict_do_update(index_elements=["tenant_id"], set_=set_)
    )
    session.flush()


def delta_sync(
    session: Session,
    source: TenantSource,
    store_view_ids: list[int],
) -> DeltaSyncReport:
    """Aplica los cambios pendientes desde el último watermark.

    El watermark solo avanza cuando la página se aplicó por completo: si algo
    falla a mitad, el reintento vuelve a traer esos cambios. Reaplicar un cambio
    es inofensivo porque todo el camino es upsert por
    (tenant, sku, store_view).

    Recibe un `TenantSource` y no `(client, tenant_id)` para que el catálogo que
    se lee y el espejo en el que se escribe no puedan ser de tenants distintos.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = DeltaSyncReport(watermark=_read_watermark(session, tenant_id))

    # El instante se toma ANTES de la primera petición, no después de la
    # última: cualquier versión que se active mientras dura el recorrido tiene
    # que quedar del lado no-leído de la ventana, para que la próxima pasada la
    # traiga. Tomarlo al final la dejaría fuera para siempre.
    read_started_at = datetime.now(UTC)
    since_timestamp = _since_timestamp(_read_last_delta_read_at(session, tenant_id))

    for page in client.iter_deltas(report.watermark, since_timestamp=since_timestamp):
        # Un SKU puede aparecer varias veces en la misma página; solo interesa
        # su último estado, y gana el último evento, sea cual sea (last-event-wins),
        # no "delete" de forma absoluta: un delete seguido de un save significa que
        # el SKU se borró y se volvió a crear, y ahí debe ganar el save. Esto solo
        # es correcto porque el endpoint de deltas garantiza los items en orden
        # ascendente de change_id; no se ordena aquí a propósito, para que una
        # regresión real de esa garantía se note en vez de quedar oculta.
        last_event: dict[str, str] = {}
        for change in page["items"]:
            report.changes_seen += 1
            last_event[change["sku"]] = change["event"]

        to_delete = [sku for sku, event in last_event.items() if event == "delete"]
        to_refresh = [sku for sku, event in last_event.items() if event != "delete"]

        if to_delete:
            result = session.execute(
                delete(ProductRecord).where(
                    ProductRecord.tenant_id == tenant_id,
                    ProductRecord.sku.in_(to_delete),
                )
            )
            report.records_deleted += result.rowcount or 0

        # Set de SKUs cuyas categorías ya se reemplazaron en esta página. La
        # asignación producto-categoría es GLOBAL (sin store view), así que
        # aplicarla una vez por SKU es correcto; el bucle de abajo itera por
        # store view para `upsert_record`, y sin este guard se repetiría el
        # mismo reemplazo de conjunto una vez por cada store view, trabajo
        # idéntico redundante.
        categorized: set[str] = set()

        for store_id in store_view_ids:
            for item in client.products_by_sku(store_id, to_refresh):
                identity = ProductIdentity(
                    sku=item["sku"],
                    mpn=item.get("mpn"),
                    model=item.get("model"),
                    gtin=item.get("gtin"),
                    variant_key=item.get("variant_key"),
                )
                effective, provenance = resolve_scope(
                    item["global_values"], item["store_values"]
                )
                magento_updated_at = parse_magento_datetime(item.get("updated_at"))
                if magento_updated_at is None:
                    note_unreadable_timestamp(report, item["sku"])
                upsert_record(
                    session, tenant_id, store_id, identity, effective, provenance,
                    magento_updated_at,
                    attribute_set_id=item.get("attribute_set_id"),
                    type_id=item.get("type_id"),
                    website_ids=item["website_ids"],
                )
                if item["sku"] not in categorized:
                    # Conjunto completo, no alta suelta: lo que el payload no
                    # trae deja de estar asignado, igual que en `full_sync`.
                    # Una lista vacía es un estado legítimo ("sin categorías")
                    # y debe dejar al producto sin asignaciones, no saltarse.
                    set_product_categories(
                        session, tenant_id, item["sku"], item["category_ids"]
                    )
                    categorized.add(item["sku"])
                report.records_updated += 1

        # Una página cuyos items son SOLO activaciones de versión llega con
        # `last_change_id: null` a propósito (ver `DeltaReader::getChanges()` y
        # `MagentoClient.iter_deltas`): esas filas no son de la cola y no deben
        # mover el watermark de change_id. Tomarlo tal cual lo pondría en NULL
        # y la próxima pasada reprocesaría la cola entera desde cero.
        if page["last_change_id"] is not None:
            report.watermark = page["last_change_id"]
        _write_watermark(session, tenant_id, report.watermark)
        session.commit()

    # Al final y no por página: el recorrido terminó sin excepción, así que
    # todo lo que se activó hasta `read_started_at` ya se aplicó. Se escribe
    # incluso cuando no hubo ni una página, porque "no había nada" también es
    # haber leído hasta acá; si no, la ventana crecería sin límite en un
    # catálogo tranquilo.
    _write_watermark(session, tenant_id, report.watermark, delta_read_at=read_started_at)
    session.commit()

    return report
