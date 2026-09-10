from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.models import ProductRecord
from skudo.mirror.signals import upsert_signals

# Ventana por defecto de la agregación comercial, en días. La misma que el
# módulo aplica cuando no se le pasa nada, declarada acá para que el valor sea
# explícito en el reporte y no una constante escondida en PHP.
DEFAULT_SIGNAL_WINDOW_DAYS = 90


class SignalSyncReport(BaseModel):
    store_views_read: int = 0
    signals_written: int = 0
    # A1: SKUs con señal para los que el espejo no tiene NI UN
    # `product_record` en NINGUNA store view. El contrato de `/signals` dice
    # que su población es la de `/products` (ver
    # `SignalReader::restrictToCatalogPopulation()`), así que esto debería
    # ser siempre 0 y una violación del contrato tiene que ser VISIBLE, no
    # silenciosa. No se descartan las filas: descartarlas del lado del
    # espejo escondería el desacuerdo entre los dos lados, que es
    # exactamente lo que dejó pasar el hallazgo. Se cuentan y se nombran.
    signals_without_product_record: int = 0
    skus_without_product_record: list[str] = []


def sync_signals(
    session: Session,
    source: TenantSource,
    store_view_ids: list[int],
    days: int = DEFAULT_SIGNAL_WINDOW_DAYS,
) -> SignalSyncReport:
    """Vuelca las señales comerciales por store view en el espejo.

    Sin esto, `product_signal` no tenía NINGÚN camino de ingesta:
    `upsert_signals` solo se llamaba desde tests, así que la tabla, sus
    pruebas y su documentación afirmaban un dato que en producción no llegaba
    nunca. Es el mismo hallazgo que dejó `attributes`, `attribute_options`,
    `option_labels` y `categories` vacías una fase antes.

    Una pasada POR STORE VIEW, no una global: `SignalReader::getSignals()`
    filtra las ventas por `o.store_id`, resuelve el stock vendible por el
    canal de venta del website de esa tienda y lee la demanda de búsqueda de
    su propio `search_query`. Pedir una sola tienda y replicar el resultado
    inventaría la señal comercial de las demás.

    Recibe un `TenantSource` y no `(client, tenant_id)` sueltos, por la misma
    razón que `full_sync`/`delta_sync`/`sync_attributes`: para que el catálogo
    que se lee y el tenant en el que se escribe no puedan desemparejarse.

    Los `null` del módulo se pasan TAL CUAL a `upsert_signals`, que los
    escribe en columnas nullable: `revenue` nulo significa que hay ítems de
    pedido sin importe y la suma no es confiable; `search_demand` nulo, que
    esa tienda no tiene datos de búsqueda en la ventana. Colapsarlos en cero
    sería afirmar una medición que nadie hizo.

    No hay barrido por VENTA: un SKU que deja de vender simplemente deja de
    aparecer en la respuesta, y su fila anterior sigue siendo el último hecho
    observado —fechado por `observed_at`—, no una mentira. Decidir cuándo una
    señal caduca es del consumidor, no del espejo.

    Sí lo hay, desde M3, por EXISTENCIA: una señal de un SKU que el espejo ya
    no contiene no describe nada, y S1 prioriza por señal. La borran
    `mirror.signals.delete_orphan_signals` (al final de la pasada completa) y
    el camino de borrado de `delta_sync`. Son dos preguntas distintas: dejó de
    vender / dejó de existir.

    Contrato de población (A1), la mitad de este lado: `/signals` promete
    servir la MISMA población que `/products` — el módulo la recorta
    explícitamente (`SignalReader::restrictToCatalogPopulation()`), porque
    `sales_order_item` no es una tabla versionada y sin el recorte devolvía
    SKUs que ningún otro endpoint puede describir. Verificado sobre HTTP
    real: el espejo quedaba con una fila de `product_signal` para un SKU sin
    un solo `product_record` en ninguna store view, y cualquier priorización
    "por dinero" que una las dos tablas lo perdía o lo unía mal.

    Este lado no vuelve a implementar el recorte —dos definiciones de la
    misma población compitiendo es el defecto C2 otra vez— pero tampoco
    confía a ciegas: cuenta los SKUs con señal que el espejo no puede
    describir y los NOMBRA en el reporte. Si el número deja de ser 0, el
    contrato se rompió en algún lado y se ve en el reporte de la pasada, no
    tres fases después en una consulta que une las dos tablas.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = SignalSyncReport()

    signalled_skus: set[str] = set()
    for store_id in store_view_ids:
        rows = client.signals(store_id, days=days)
        signalled_skus.update(str(row["sku"]) for row in rows)
        report.store_views_read += 1
        report.signals_written += upsert_signals(session, tenant_id, store_id, rows)

    if signalled_skus:
        # Una sola consulta al final, no una por store view: la pregunta es
        # "¿el espejo puede describir este SKU en ALGUNA store view?", que es
        # la condición que hace utilizable una fila de `product_signal`.
        mirrored = set(
            session.scalars(
                select(ProductRecord.sku).where(
                    ProductRecord.tenant_id == tenant_id,
                    ProductRecord.sku.in_(sorted(signalled_skus)),
                )
            ).all()
        )
        orphans = sorted(signalled_skus - mirrored)
        report.signals_without_product_record = len(orphans)
        report.skus_without_product_record = orphans

    session.commit()
    return report
