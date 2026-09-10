import hashlib
import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductRecord


class ProductIdentity(BaseModel):
    """Identidad desglosada. Todo texto: normalizar a número destruye la identidad."""

    sku: str
    mpn: str | None = None
    model: str | None = None
    gtin: str | None = None
    variant_key: str | None = None


# Vocabulario cerrado de procedencia de scope. Es la escala en la que el valor
# efectivo se fijó, y por tanto la escala en la que habría que corregirlo.
SCOPE_GLOBAL = "global"
SCOPE_WEBSITE = "website"
SCOPE_STORE = "store"
SCOPE_UNKNOWN = "desconocido"

SCOPE_PROVENANCE_VOCABULARY = frozenset(
    {SCOPE_GLOBAL, SCOPE_WEBSITE, SCOPE_STORE, SCOPE_UNKNOWN}
)

# Qué procedencia corresponde a una fila EAV de store view, según el scope que
# el atributo DECLARA en `eav_attribute.is_global`.
_OVERRIDE_PROVENANCE = {
    SCOPE_STORE: SCOPE_STORE,
    SCOPE_WEBSITE: SCOPE_WEBSITE,
}


def _override_provenance(declared_scope: str | None) -> str:
    """La escala en la que se fijó un valor que tiene fila de store view.

    Magento persiste un atributo con scope de WEBSITE escribiendo una fila EAV
    para CADA store view de ese website, así que desde
    `catalog_product_entity_*` un override de website y uno de tienda son
    indistinguibles: lo único que los separa es el `declared_scope` del
    atributo. En el catálogo de referencia hay 24 atributos de producto con
    `is_global = 2` —`price` entre ellos, porque `catalog/price/scope` está en
    Website—, y llamarlos "store" le daría a S3 una respuesta con confianza
    equivocada sobre dónde corregir. El spec §5.6: corregir en el scope
    equivocado es el error de write-back más común en Magento.

    Los dos casos que devuelven DESCONOCIDO:

    - El atributo no está en el espejo (`sync_attributes` no corrió todavía, o
      corrió después): no se sabe en qué escala se fijó el valor, y "store"
      sería una respuesta confiada y posiblemente falsa.
    - El atributo se declara `global` pero tiene fila de store view: el origen
      se contradice. Decir "global" mentiría sobre el VALOR (la fila de tienda
      existe y gana); decir "store" mentiría sobre la ESCALA.
    """
    return _OVERRIDE_PROVENANCE.get(declared_scope or "", SCOPE_UNKNOWN)


def resolve_scope(
    global_values: dict[str, str],
    store_values: dict[str, str],
    declared_scopes: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Resuelve el valor efectivo y registra en qué escala se fijó.

    La presencia de una clave en `store_values` decide que NO es herencia
    global, no su contenido: un valor vacío puesto en la store view es un valor
    puesto ahí, y colapsarlo con la herencia global ocultaría un defecto de
    traducción.

    `declared_scopes` es `{codigo_atributo: "global"|"website"|"store"}`, tal
    como `sync_attributes` lo espeja desde `eav_attribute.is_global`. Sin él,
    todo override queda como DESCONOCIDO: un llamador que no conoce las escalas
    no puede afirmar ninguna. Ver `_override_provenance`.
    """
    declared_scopes = declared_scopes or {}
    effective: dict[str, str] = dict(global_values)
    provenance: dict[str, str] = {code: SCOPE_GLOBAL for code in global_values}

    for code, value in store_values.items():
        effective[code] = value
        provenance[code] = _override_provenance(declared_scopes.get(code))

    return effective, provenance


def content_hash(
    identity: ProductIdentity, effective: dict, store_view_magento_id: int
) -> str:
    """Hash estable del contenido relevante. Base del caché de la capa IA en S6.

    La store view entra en el material hasheado porque el veredicto sobre un
    mismo texto depende de la tienda: texto idéntico en PY y BR es exactamente
    el defecto de "nombre sin traducir". Sin ella, un caché con esta clave
    devolvería el veredicto de la tienda española para la portuguesa, justo en
    la población que más hay que juzgar.
    """
    material = json.dumps(
        {
            "store_view_magento_id": store_view_magento_id,
            "identity": identity.model_dump(),
            "attributes": effective,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# Columnas que un upsert ACTUALIZA cuando la fila ya existe. La clave
# (tenant, sku, store view) y `sync_generation` quedan fuera de esta lista: la
# primera por definición, y el sello sólo se actualiza cuando la pasada
# completa lo manda (ver `record_values`).
UPDATABLE_COLUMNS = (
    "mpn", "model", "gtin", "variant_key", "attribute_set_id", "type_id",
    "website_ids", "attributes", "scope_provenance", "content_hash",
    "magento_updated_at", "mirrored_at",
)

# Clave natural de la fila del espejo, y por tanto el conflicto del upsert.
RECORD_KEY = ("tenant_id", "sku", "store_view_magento_id")


def record_values(
    tenant_id: int,
    store_view_magento_id: int,
    identity: ProductIdentity,
    effective: dict,
    provenance: dict,
    magento_updated_at: datetime | None,
    *,
    attribute_set_id: int | None = None,
    type_id: str | None = None,
    website_ids: list[int] | None = None,
    sync_generation: int | None = None,
) -> dict:
    """La fila del espejo, como diccionario de valores.

    UNA sola definición de qué es una fila de `product_record`, compartida por
    la escritura de a una (`upsert_record`) y la de una página entera
    (`upsert_records`). Antes de H3 había una sola escritura y la definición
    vivía dentro de ella; con dos caminos, duplicar el diccionario sería
    duplicar la definición del objeto central del sistema — el defecto C2 en
    otra forma.

    `attribute_set_id` y `type_id` son opcionales porque su ausencia es un
    hecho legítimo: la sonda puede no informarlos y NULL dice 'desconocido'.
    `website_ids` sigue el mismo patrón: es la pieza que le falta a
    `derive_category_effect` para evaluar su tercera condición.

    `sync_generation` solo lo pasa una pasada completa, que necesita sellar lo
    que tocó para poder barrer lo que no. Sin él, la clave no aparece en el
    diccionario y la fila conserva el sello que tuviera: una escritura
    incremental no puede hacer parecer viva a una fila que la próxima pasada
    completa no encuentre, ni al revés.
    """
    values: dict = {
        "tenant_id": tenant_id,
        "store_view_magento_id": store_view_magento_id,
        "sku": identity.sku,
        "mpn": identity.mpn,
        "model": identity.model,
        "gtin": identity.gtin,
        "variant_key": identity.variant_key,
        "attribute_set_id": attribute_set_id,
        "type_id": type_id,
        "website_ids": website_ids,
        "attributes": effective,
        "scope_provenance": provenance,
        "content_hash": content_hash(identity, effective, store_view_magento_id),
        "magento_updated_at": magento_updated_at,
        # clock_timestamp() y no now(): now() es de alcance transaccional en
        # Postgres, así que dos upserts en la misma transacción escribirían la
        # misma hora y `mirrored_at` mentiría sobre la frescura del espejo.
        # Con commit por página eso serían 500 filas con la misma hora.
        "mirrored_at": func.clock_timestamp(),
    }
    if sync_generation is not None:
        values["sync_generation"] = sync_generation
    return values


def upsert_records(session: Session, rows: list[dict]) -> int:
    """Escribe una PÁGINA entera en una sola sentencia multi-fila.

    Por qué existe (H3, medido): con una sentencia por producto, el espejo
    escribía a ~200 productos/s y el coste dominante no era la base sino la
    COMPILACIÓN del `INSERT ... ON CONFLICT` en SQLAlchemy, una vez por
    producto (`visit_insert` era 6,3 s de 19 s en el perfil de 2.000
    productos). Para el catálogo piloto —228.881 productos × 2 store views—
    eso son ~36 minutos de puro armado de SQL. Con la página en una sentencia
    se compila una vez cada 500 filas y el mismo trabajo va a ~2.100
    productos/s.

    Dos detalles que no son cosméticos:

    1. SKUs REPETIDOS en el mismo lote. Postgres rechaza un
       `ON CONFLICT DO UPDATE` que afecte la misma fila dos veces en la misma
       sentencia ("cannot affect row a second time"), mientras que la versión
       de a una fila los aplicaba en orden y ganaba el último. Se deduplica
       por clave conservando el ÚLTIMO, que es exactamente lo que hacía el
       bucle anterior: el cambio de forma no cambia el resultado. Un lote con
       el mismo SKU dos veces no debería llegar —`/products` pagina por clave
       de entidad y `delta_sync` deduplica antes de pedir— pero un error del
       otro lado no debe volverse una excepción de Postgres a mitad de una
       pasada de tres horas.
    2. El lote tiene que ser HOMOGÉNEO en columnas: un `INSERT` multi-fila
       tiene una sola lista de columnas. Mezclar filas con y sin
       `sync_generation` haría que unas heredaran el valor de otras, que es
       justo el sello del barrido. Se comprueba y se falla ruidosamente.
    """
    if not rows:
        return 0

    columns = set(rows[0])
    mismatched = [row for row in rows if set(row) != columns]
    if mismatched:
        raise ValueError(
            "un lote de upsert tiene que ser homogéneo en columnas: un INSERT "
            "multi-fila tiene una sola lista de columnas, así que una fila con "
            "otras claves heredaría los valores de sus vecinas. Diferencias: "
            f"{sorted(columns ^ set(mismatched[0]))}"
        )

    deduplicated: dict[tuple, dict] = {}
    for row in rows:
        deduplicated[tuple(row[column] for column in RECORD_KEY)] = row

    updatable = list(UPDATABLE_COLUMNS)
    if "sync_generation" in columns:
        updatable.append("sync_generation")

    stmt = insert(ProductRecord).values(list(deduplicated.values()))
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=list(RECORD_KEY),
            set_={column: getattr(stmt.excluded, column) for column in updatable},
        )
    )
    session.flush()
    return len(deduplicated)


def get_record(
    session: Session, tenant_id: int, sku: str, store_view_magento_id: int
) -> ProductRecord | None:
    return session.scalar(
        select(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.sku == sku,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
