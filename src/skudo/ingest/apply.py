"""El bucle que traduce items de `/products` a filas del espejo.

Existía tres veces —una en `full_sync`, una en `delta_sync` y ninguna
reutilizable— y por eso la reparación DIRIGIDA de una partición divergente no
se podía escribir: `reconcile()` sabe desde H1 QUÉ particiones de 256
divergen, y el único remedio seguía siendo `full_sync`. Detección que nombra
un lugar con un remedio que lo ignora es media función.

Acá vive una sola vez, y los tres caminos la comparten:

- `full_sync`: página a página del recorrido por cursor.
- `delta_sync`: el conjunto de SKUs que la cola de cambios nombró.
- `repair_partitions`: los SKUs de las particiones que divergen.

`sync_generation` solo lo pasa la pasada completa, que necesita sellar lo que
toca para poder barrer lo que no. Los otros dos caminos lo dejan en None a
propósito: una escritura incremental no debe hacer parecer viva a una fila
que la próxima pasada completa no va a encontrar, ni al revés.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from skudo.mirror.categories import set_products_categories
from skudo.mirror.products import ProductIdentity, record_values, resolve_scope, upsert_records

# Cuántos SKUs con fecha ilegible se nombran en el reporte. Un conteo solo dice
# que hay filas afectadas; una muestra acotada dice cuáles mirar.
UNTIMESTAMPED_SAMPLE_SIZE = 20


def parse_magento_datetime(raw: str | None) -> datetime | None:
    """Magento devuelve 'YYYY-MM-DD HH:MM:SS' en UTC, sin zona explícita.

    Política explícita para lo que no se puede interpretar —la cadena vacía y el
    '0000-00-00 00:00:00' que MySQL admite y los catálogos heredados contienen—:
    es DESCONOCIDO, y se devuelve None. Ni una excepción, que abortaría la
    página entera y en `delta_sync` bloquearía el avance del watermark y con él
    toda sincronización posterior; ni una fecha de relleno, que sería un dato
    falso con aspecto confiable. Quien llama reporta el caso.

    Se acepta —y se DESCARTA— una fracción de segundo. `catalog_product_entity
    .updated_at` es un `timestamp` sin precisión fraccionaria, así que en
    Magento tal como se instala esto no pasa nunca; pasa si un tenant alteró la
    columna. El caso importa por H1: el digest de contenido de `/checksums`
    canonicaliza el timestamp al segundo (`ContentDigest::timestampToken()`) y
    este lado tiene que llegar al MISMO texto desde el valor que guardó. Si acá
    la fracción hiciera fallar el parseo, el espejo guardaría NULL, su token
    sería 'desconocido' contra un timestamp real del otro lado, y la
    reconciliación reportaría deriva PERMANENTE que ningún re-sync limpiaría.
    """
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate or candidate.startswith("0000-00-00"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            # Sin microsegundos: es la misma canonicalización al segundo que
            # hace el módulo, en el mismo lugar del ciclo.
            return datetime.strptime(candidate, fmt).replace(microsecond=0, tzinfo=UTC)
        except ValueError:
            continue
    return None


def note_unreadable_timestamp(report, sku: str) -> None:
    """Anota en el reporte que un SKU llegó sin fecha interpretable."""
    report.records_without_timestamp += 1
    if len(report.skus_without_timestamp) < UNTIMESTAMPED_SAMPLE_SIZE:
        report.skus_without_timestamp.append(sku)


def apply_items(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    items: list[dict],
    scopes: dict[str, str],
    report,
    *,
    sync_generation: int | None = None,
    categorized: set[str] | None = None,
) -> int:
    """Escribe en el espejo los items de una respuesta del módulo.

    EN LOTE: las filas del lote entero se escriben en una sentencia multi-fila
    (`upsert_records`) y sus categorías en dos (`set_products_categories`). De
    a una sentencia por producto, el espejo escribía a ~200 productos/s y el
    coste dominante era la compilación del SQL en SQLAlchemy —una vez por
    producto—, no la base: para el catálogo piloto, ~36 minutos de puro armado
    de SQL. Medido de nuevo con el lote: ~2.100 productos/s. Ver
    `mirror.products.upsert_records`.

    No hace `commit()` ni `rollback()`: la unidad de trabajo la decide quien
    llama —`full_sync` confirma por página, `delta_sync` por página de cola—
    para que el punto de reanudación y los datos avancen en la misma
    transacción.

    `categorized`, cuando se pasa, es el conjunto de SKUs cuyas categorías ya
    se reemplazaron en esta unidad de trabajo. La asignación
    producto-categoría es GLOBAL (sin store view), así que aplicarla una vez
    por SKU basta; sin el conjunto, un llamador que itera por store view
    repetiría el mismo reemplazo una vez por tienda.

    Devuelve cuántos items se aplicaron. Del reporte solo toca la
    contabilidad de fechas ilegibles (`records_without_timestamp`,
    `skus_without_timestamp`), que es idéntica en los tres caminos; el conteo
    de escrituras lo suma cada llamador en SU campo, porque no se llama igual
    en los tres reportes y unificarlo acá solo habría renombrado campos que
    ya están en uso.
    """
    rows: list[dict] = []
    assignments: dict[str, list[int]] = {}

    for item in items:
        identity = ProductIdentity(
            sku=item["sku"],
            mpn=item.get("mpn"),
            model=item.get("model"),
            gtin=item.get("gtin"),
            variant_key=item.get("variant_key"),
        )
        effective, provenance = resolve_scope(
            item["global_values"], item["store_values"], scopes
        )
        magento_updated_at = parse_magento_datetime(item.get("updated_at"))
        if magento_updated_at is None:
            note_unreadable_timestamp(report, item["sku"])
        rows.append(
            record_values(
                tenant_id,
                store_view_magento_id,
                identity,
                effective,
                provenance,
                magento_updated_at,
                attribute_set_id=item.get("attribute_set_id"),
                type_id=item.get("type_id"),
                website_ids=item["website_ids"],
                sync_generation=sync_generation,
            )
        )
        if categorized is None or item["sku"] not in categorized:
            # Conjunto completo, no alta suelta: lo que el payload no trae
            # deja de estar asignado. Una lista vacía es un estado legítimo
            # ("sin categorías") y debe dejar al producto sin asignaciones,
            # no saltarse.
            assignments[item["sku"]] = item["category_ids"]
            if categorized is not None:
                categorized.add(item["sku"])

    upsert_records(session, rows)
    set_products_categories(session, tenant_id, assignments)

    return len(items)
