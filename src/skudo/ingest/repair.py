"""Reparación DIRIGIDA de las particiones que `reconcile()` nombra.

H1 partió el digest de contenido en 256 particiones por hash del SKU y dejó
que `reconcile()` reportara QUÉ particiones divergen. El remedio, sin
embargo, seguía siendo `full_sync`: 228.881 productos y horas de trabajo para
reparar ~900. Detección que nombra un lugar con un remedio que lo ignora es
media función, y ésta es la otra mitad.

La reparación de una partición son tres pasos, y el orden importa:

1. Se le pide al módulo la población AUTORITATIVA de esa partición
   (`/checksums?partitions=ab,cd`, H3). Es la misma consulta que produce el
   digest, así que el conjunto y el digest son del mismo instante.
2. Se borran del espejo, EN ESA STORE VIEW, los SKUs de esas particiones que
   la instancia ya no ofrece. Sin este paso la reparación no podría cerrar
   una divergencia por sobra, sólo por valor rancio.
3. Se releen con `/products-by-sku` los SKUs que sí existen y se aplican con
   el mismo bucle que usa la pasada completa (`apply_items`).

Lo que esta reparación NO hace, a propósito: no toca `sync_generation`. El
sello es de la pasada completa, y una escritura dirigida que lo moviera haría
parecer viva —para el barrido de la próxima pasada— una fila que esa pasada
puede no encontrar. Es la misma razón por la que `delta_sync` tampoco lo
toca.
"""

import re

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from skudo.ingest.apply import apply_items
from skudo.ingest.reconcile import DriftReport, partition_of
from skudo.ingest.source import TenantSource
from skudo.mirror.attributes import declared_scopes
from skudo.mirror.categories import delete_orphan_category_assignments
from skudo.mirror.models import ProductRecord
from skudo.mirror.signals import delete_orphan_signals

# Forma de una partición: exactamente lo que emite `partition_of()` —dos
# caracteres hex en minúsculas—. Se valida ACÁ, antes de la petición, para que
# un error de tipeo del operador falle con un mensaje que lo nombra en vez de
# con un 400 del módulo que hay que ir a leer al otro lado del cable. No se
# normaliza 'FF' a 'ff': un llamador que manda mayúsculas está calculando la
# partición de otra forma, y taparlo esconde ese desacuerdo.
PARTITION_PATTERN = re.compile(r"^[0-9a-f]{2}$")

# Particiones por llamada a `/checksums`. El módulo topa en 32
# (`ChecksumReader::MAX_PARTITIONS`) porque pedir más es pedir una fracción
# grande del catálogo por un endpoint de reconciliación; acá se trocea para
# respetar ese tope sin que el llamador tenga que conocerlo.
PARTITIONS_PER_CHECKSUMS_CALL = 32


class RepairReport(BaseModel):
    store_view_magento_id: int
    partitions: list[str]
    # SKUs que la instancia ofrece en esas particiones y que se releyeron.
    skus_reread: int = 0
    records_written: int = 0
    # Filas del espejo borradas por no existir ya en la partición.
    records_deleted: int = 0
    # M3: lo que esas filas dejaban huérfano cuando el SKU no queda en NINGUNA
    # store view.
    category_assignments_deleted: int = 0
    signals_deleted: int = 0
    # SKUs que el módulo dijo tener y que `/products-by-sku` no devolvió. No
    # debería pasar (las dos respuestas salen de la misma población) y por eso
    # se cuenta y se nombra en vez de descartarse: un número distinto de cero
    # es un desacuerdo entre dos endpoints del mismo módulo, y esconderlo es
    # el hallazgo A1 otra vez.
    skus_not_returned: list[str] = []
    records_without_timestamp: int = 0
    skus_without_timestamp: list[str] = []


def partitions_needing_repair(drift: DriftReport) -> list[str]:
    """Las particiones divergentes de un reporte de reconciliación.

    Existe para que el operador no tenga que copiar nombres a mano desde la
    salida de `reconcile`: el remedio se alimenta de la detección.
    """
    return [entry.partition for entry in drift.diverging_partitions]


def _validate(partitions: list[str]) -> list[str]:
    if not partitions:
        raise ValueError(
            "no se pidió ninguna partición: reparar 'nada' en silencio dejaría "
            "creer que la divergencia se atendió. Pasá las particiones que "
            "`reconcile` reportó como divergentes."
        )
    invalid = [p for p in partitions if not PARTITION_PATTERN.match(p)]
    if invalid:
        raise ValueError(
            f"particiones con forma inválida: {invalid}. Se esperaban dos "
            "caracteres hex en minúsculas ('00'..'ff'), tal como los emite "
            "`partition_of()` y el digest del módulo."
        )
    # Se preserva el orden de llegada y se quitan repetidos: pedir dos veces
    # la misma partición releería dos veces los mismos SKUs.
    seen: dict[str, None] = {}
    for partition in partitions:
        seen.setdefault(partition, None)
    return list(seen)


def _authoritative_skus(
    source: TenantSource, store_view_magento_id: int, partitions: list[str]
) -> dict[str, list[str]]:
    """Los SKUs que la instancia tiene en cada partición pedida.

    Se EXIGE que la respuesta traiga `partition_skus`, igual que
    `reconcile._remote_partitions` exige el digest de contenido: un módulo sin
    ese campo haría creer que la partición está vacía, y el paso 2 borraría del
    espejo la cohorte entera. Reportar (o reparar) sobre una respuesta que no
    trae lo que se pidió es la forma exacta del defecto H1.
    """
    out: dict[str, list[str]] = {}
    for start in range(0, len(partitions), PARTITIONS_PER_CHECKSUMS_CALL):
        chunk = partitions[start : start + PARTITIONS_PER_CHECKSUMS_CALL]
        remote = source.client.checksums(store_view_magento_id, partitions=chunk)
        if "partition_skus" not in remote:
            raise RuntimeError(
                "/checksums no trae `partition_skus`: este módulo no publica "
                "los SKUs de una partición (Standard_Skudo lo declara desde "
                "H3), así que la reparación dirigida no puede saber qué SKUs "
                "existen. Se aborta en vez de tomar la ausencia por una "
                "partición vacía, que borraría del espejo la cohorte entera. "
                f"Claves recibidas: {sorted(remote)}"
            )
        served = {
            str(row["partition"]): [str(sku) for sku in row["skus"]]
            for row in remote["partition_skus"]
        }
        missing = [partition for partition in chunk if partition not in served]
        if missing:
            raise RuntimeError(
                f"/checksums no devolvió las particiones {missing} que se le "
                "pidieron. El módulo emite una entrada por partición pedida, "
                "incluidas las vacías; tomar la ausencia por vacío borraría "
                "del espejo los SKUs de esas particiones."
            )
        out.update({partition: served[partition] for partition in chunk})
    return out


def repair_partitions(
    session: Session,
    source: TenantSource,
    store_view_magento_id: int,
    partitions: list[str],
) -> RepairReport:
    """Relee del origen sólo los SKUs de `partitions` y repara esa cohorte.

    Recibe un `TenantSource` y no `(client, tenant_id)` por la misma razón que
    el resto del ingestor: para que el catálogo que se lee y el espejo en el
    que se escribe no puedan ser de tenants distintos.
    """
    partitions = _validate(partitions)
    tenant_id = source.tenant_id
    report = RepairReport(
        store_view_magento_id=store_view_magento_id, partitions=partitions
    )

    authoritative = _authoritative_skus(source, store_view_magento_id, partitions)
    wanted = {sku for skus in authoritative.values() for sku in skus}

    # Los SKUs del espejo se filtran EN PYTHON y no con un `WHERE` sobre el
    # hash: la partición es sha256 del SKU, que Postgres no calcula sin
    # pgcrypto, y una segunda definición del particionado del lado SQL es
    # justamente la forma de que los dos lados dejen de coincidir.
    mirrored = [
        sku
        for sku in session.scalars(
            select(ProductRecord.sku).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
            )
        ).all()
        if partition_of(sku) in authoritative
    ]

    stale = sorted(set(mirrored) - wanted)
    if stale:
        result = session.execute(
            delete(ProductRecord).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
                ProductRecord.sku.in_(stale),
            )
        )
        report.records_deleted = result.rowcount or 0
        # M3. La reparación borra filas de UNA store view, así que la lista de
        # SKUs no alcanza como criterio: el producto puede seguir vivo en la
        # otra tienda y su asignación —que es global— seguir siendo verdadera.
        # Se usa el MISMO predicado referencial que la pasada completa,
        # acotado a esos SKUs para no recorrer la tabla entera por una cohorte
        # de un puñado de filas.
        report.category_assignments_deleted = delete_orphan_category_assignments(
            session, tenant_id, skus=stale
        )
        report.signals_deleted = delete_orphan_signals(session, tenant_id, skus=stale)

    scopes = declared_scopes(session, tenant_id)
    to_reread = sorted(wanted)
    report.skus_reread = len(to_reread)
    if to_reread:
        items = source.client.products_by_sku(store_view_magento_id, to_reread)
        report.records_written = apply_items(
            session, tenant_id, store_view_magento_id, items, scopes, report
        )
        returned = {str(item["sku"]) for item in items}
        report.skus_not_returned = sorted(wanted - returned)

    session.commit()
    return report
