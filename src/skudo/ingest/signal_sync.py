from pydantic import BaseModel
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.signals import upsert_signals

# Ventana por defecto de la agregación comercial, en días. La misma que el
# módulo aplica cuando no se le pasa nada, declarada acá para que el valor sea
# explícito en el reporte y no una constante escondida en PHP.
DEFAULT_SIGNAL_WINDOW_DAYS = 90


class SignalSyncReport(BaseModel):
    store_views_read: int = 0
    signals_written: int = 0


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

    No hay barrido: un SKU que deja de vender simplemente deja de aparecer en
    la respuesta, y su fila anterior sigue siendo el último hecho observado
    —fechado por `observed_at`—, no una mentira. Decidir cuándo una señal
    caduca es del consumidor, no del espejo.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = SignalSyncReport()

    for store_id in store_view_ids:
        rows = client.signals(store_id, days=days)
        report.store_views_read += 1
        report.signals_written += upsert_signals(session, tenant_id, store_id, rows)

    session.commit()
    return report
