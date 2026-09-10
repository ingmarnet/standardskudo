import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.models import ProductRecord

# Número de particiones del digest de contenido. CONSTANTE, no configuración:
# un número configurable es una forma de que los dos lados particionen distinto
# y comparen manzanas con naranjas. Debe coincidir con
# `ContentDigest::PARTITION_COUNT` del módulo Magento, y `reconcile()` EXIGE
# que el módulo declare el mismo número en vez de asumirlo.
PARTITION_COUNT = 256

# Token de un `updated_at` que no se puede leer. Idéntico a
# `ContentDigest::UNKNOWN_TIMESTAMP` del módulo. No es una fecha de relleno
# —eso sería un dato falso con aspecto confiable— ni la cadena vacía, que se
# confundiría con "no vino el campo".
UNKNOWN_TIMESTAMP_TOKEN = "desconocido"

# Forma textual canónica del `updated_at` de Magento. Al segundo, sin zona: es
# lo que emite MySQL para una columna `timestamp` y lo que el módulo pone en el
# digest. La fracción de segundo se descarta en los DOS lados (ver
# `ContentDigest::timestampToken()`): coincidir al segundo es mejor que
# discrepar para siempre, que es lo que pasaría si un lado pudiera reproducir
# la fracción y el otro no.
CANONICAL_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class PartitionDrift(BaseModel):
    """Una partición en la que los dos lados no coinciden.

    `None` en un lado significa que la partición no existe ahí: el módulo solo
    emite las particiones no vacías, así que un SKU que existe únicamente en el
    espejo (o únicamente en Magento) aparece como una partición de un solo
    lado. Se reporta como divergencia, no se omite.
    """

    partition: str
    magento_count: int | None
    mirror_count: int | None
    magento_digest: str | None
    mirror_digest: str | None


class DriftReport(BaseModel):
    store_view_magento_id: int
    magento_count: int
    mirror_count: int
    digest_matches: bool
    needs_full_sync: bool
    # H1: el resultado a nivel de CONTENIDO, al lado del de conjunto y no en
    # su lugar. `partition_count` es el esquema con el que se comparó (se
    # exige el acuerdo con el módulo), `partitions_compared` cuántas
    # particiones existían en alguno de los dos lados, y
    # `diverging_partitions` las que no coinciden — con su conteo y su digest
    # de cada lado, para que la investigación empiece con datos y no con una
    # bandera booleana.
    partition_count: int
    partitions_compared: int
    content_matches: bool
    diverging_partitions: list[PartitionDrift]


def sku_digest(skus: list[str]) -> str:
    """Huella del conjunto de SKUs, independiente del orden.

    Se ordena antes de hashear para que Magento y el espejo puedan calcularla
    por separado y comparar sin coordinar paginación.

    Lo que esta huella NO puede ver (H1): un VALOR cambiado en un SKU que ya
    estaba. El conjunto es el mismo, así que la huella es la misma, para
    siempre. Ese punto ciego lo cierra `content_partitions()`.
    """
    joined = "\n".join(sorted(skus))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def partition_of(sku: str) -> str:
    """Partición de un SKU: los dos primeros caracteres hex de su sha256.

    Depende SÓLO de los bytes UTF-8 del SKU. No de ids de entidad (que el
    espejo no guarda), no del orden de ninguna consulta, no de la colación de
    MySQL, no de la zona horaria. `hashlib.sha256(sku.encode("utf-8"))` acá y
    `hash('sha256', $sku)` en PHP dan la misma cadena hex para los mismos
    bytes, y el SKU viaja como texto sin normalizar por todo el sistema.
    Por eso el esquema es reproducible idéntico en los dos lados.
    """
    return hashlib.sha256(sku.encode("utf-8")).hexdigest()[:2]


def timestamp_token(value: datetime | None) -> str:
    """Forma textual del `updated_at` del espejo, para el digest de contenido.

    Acá vive la trampa que este proyecto ya pisó dos veces: los dos lados
    tienen que coincidir en la forma TEXTUAL exacta, y este lado NO guarda el
    texto que mandó Magento — guarda un `timestamptz` de Postgres al que llegó
    parseándolo. El `astimezone(UTC)` es lo que cierra el círculo: psycopg
    devuelve el valor con el offset de la sesión de Postgres, que puede no ser
    UTC, y renderizar ESE reloj daría una cadena distinta de la que MySQL
    entregó — un digest que discrepa por zona horaria reportaría deriva
    permanente y el re-sync que prescribe no la limpiaría nunca.

    Un valor sin zona se interpreta como UTC, que es la convención con la que
    `parse_magento_datetime` lo creó; no se asume la zona local, que
    introduciría un desplazamiento silencioso.
    """
    if value is None:
        return UNKNOWN_TIMESTAMP_TOKEN
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime(CANONICAL_TIMESTAMP_FORMAT)


def content_partitions(
    rows: list[tuple[str, datetime | None]],
) -> dict[str, tuple[int, str]]:
    """Digest de contenido por partición, con el MISMO algoritmo que el módulo.

    Devuelve `{partición: (conteo, digest)}`. Cada línea es
    `f"{sku}\\t{token}"`, las líneas de una partición se ordenan con `sorted()`
    —que sobre UTF-8 compara igual que el `sort($lines, SORT_STRING)` de PHP,
    byte a byte— y se unen con `\\n`. Ver `Model\\ContentDigest` del módulo
    para el esquema completo y para el límite declarado de lo que
    `updated_at` alcanza a ver.
    """
    lines: dict[str, list[str]] = {}
    for sku, updated_at in rows:
        lines.setdefault(partition_of(sku), []).append(
            f"{sku}\t{timestamp_token(updated_at)}"
        )

    return {
        partition: (
            len(partition_lines),
            hashlib.sha256("\n".join(sorted(partition_lines)).encode("utf-8")).hexdigest(),
        )
        for partition, partition_lines in lines.items()
    }


def _remote_partitions(remote: dict) -> dict[str, tuple[int, str]]:
    """Lee las particiones de la respuesta de `/checksums`, exigiendo acuerdo.

    Falla ruidosamente en los dos casos en que comparar no tendría sentido —un
    módulo sin digest de contenido, o uno que particiona distinto— en vez de
    reportar "sin deriva" sobre una comparación vacía. Reportar acuerdo cuando
    no se comparó nada es la forma exacta del defecto H1.
    """
    if "content_partitions" not in remote or "partition_count" not in remote:
        raise RuntimeError(
            "/checksums no trae el digest de contenido por partición "
            "(`content_partitions` y `partition_count`) que Standard_Skudo "
            "declara desde H1. Sin él, la reconciliación solo compara el "
            "CONJUNTO de SKUs y un valor cambiado en un SKU existente queda "
            "invisible para siempre; se aborta en vez de reportar 'sin "
            f"deriva'. Claves recibidas: {sorted(remote)}"
        )

    declared = remote["partition_count"]
    if declared != PARTITION_COUNT:
        raise RuntimeError(
            f"/checksums declara {declared} particiones y este ingestor calcula "
            f"{PARTITION_COUNT}. Los dos lados particionarían por criterios "
            "distintos y la comparación no significaría nada, así que se "
            "aborta en vez de reportar una divergencia (o una coincidencia) "
            "inventada."
        )

    return {
        str(row["partition"]): (int(row["product_count"]), str(row["content_digest"]))
        for row in remote["content_partitions"]
    }


def reconcile(
    session: Session, source: TenantSource, store_view_magento_id: int
) -> DriftReport:
    """Recibe un `TenantSource` y no `(client, tenant_id)` para que los conteos
    que se comparan sean del mismo tenant en los dos lados."""
    tenant_id = source.tenant_id
    remote = source.client.checksums(store_view_magento_id)

    local_rows = [
        (sku, updated_at)
        for sku, updated_at in session.execute(
            select(ProductRecord.sku, ProductRecord.magento_updated_at).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
            )
        ).all()
    ]
    local_skus = [sku for sku, _ in local_rows]

    digest_matches = sku_digest(local_skus) == remote["sku_digest"]
    count_matches = len(local_skus) == remote["product_count"]

    remote_partitions = _remote_partitions(remote)
    mirror_partitions = content_partitions(local_rows)

    diverging = []
    for partition in sorted(set(remote_partitions) | set(mirror_partitions)):
        magento = remote_partitions.get(partition)
        mirror = mirror_partitions.get(partition)
        if magento == mirror:
            continue
        diverging.append(
            PartitionDrift(
                partition=partition,
                magento_count=magento[0] if magento else None,
                mirror_count=mirror[0] if mirror else None,
                magento_digest=magento[1] if magento else None,
                mirror_digest=mirror[1] if mirror else None,
            )
        )

    return DriftReport(
        store_view_magento_id=store_view_magento_id,
        magento_count=remote["product_count"],
        mirror_count=len(local_skus),
        digest_matches=digest_matches,
        # Cualquier discrepancia DE CONJUNTO obliga a recarga completa: no se
        # sabe qué más falta. La divergencia de contenido NO la fuerza a
        # propósito: es lo que el particionado existe para evitar. Un valor
        # rancio en una partición se repara releyendo esa partición —~900
        # productos en el catálogo piloto— y no re-sincronizando 228.881, que
        # son horas y es la misma respuesta para un producto que para todos.
        needs_full_sync=not (digest_matches and count_matches),
        partition_count=PARTITION_COUNT,
        partitions_compared=len(set(remote_partitions) | set(mirror_partitions)),
        content_matches=not diverging,
        diverging_partitions=diverging,
    )
