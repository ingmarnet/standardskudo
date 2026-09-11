from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute, AttributeOption, AttributeOptionLabel


def upsert_attribute(
    session: Session, tenant_id: int, payload: dict, *, sync_generation: int
) -> None:
    """`sync_generation` es OBLIGATORIO y sin default (M3).

    Es el sello con el que la pasada marca lo que tocó, y lo que no lo lleva se
    barre al final. Un default silencioso —0, o la generación anterior— haría
    que un llamador que se olvide de pasarlo escriba filas que el barrido de su
    propia pasada borra a continuación. Se prefiere que no compile.
    """
    stmt = insert(Attribute).values(
        tenant_id=tenant_id,
        sync_generation=sync_generation,
        code=payload["code"],
        label=payload["label"],
        frontend_input=payload["frontend_input"],
        declared_scope=payload["declared_scope"],
        is_filterable=payload["is_filterable"],
        is_required=payload["is_required"],
        attribute_set_ids=payload["attribute_set_ids"],
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "code"],
            set_={
                "label": stmt.excluded.label,
                "frontend_input": stmt.excluded.frontend_input,
                "declared_scope": stmt.excluded.declared_scope,
                "is_filterable": stmt.excluded.is_filterable,
                "is_required": stmt.excluded.is_required,
                "attribute_set_ids": stmt.excluded.attribute_set_ids,
                "sync_generation": stmt.excluded.sync_generation,
            },
        )
    )
    session.flush()


# Cuántas opciones van en UNA sentencia. No es el tamaño de la página, y la
# diferencia no es cosmética: una página son 500 ATRIBUTOS, y los atributos no
# declaran cuántas opciones traen. La página más grande de la instancia de
# referencia trae ~17.000.
#
# El límite duro es del protocolo de Postgres: una sentencia admite **65.535
# parámetros**. El `INSERT` de opciones manda 4 por fila, así que más de 16.383
# opciones en una sentencia fallan con
# `number of parameters must be between 0 and 65535` — y fallan a mitad de una
# pasada, contra el catálogo real, no en la suite. Se descubrió midiendo: la
# primera versión escribía la página entera en una sentencia y reventaba en la
# página grande de este catálogo. `test_a_batch_larger_than_the_parameter_limit
# _is_written` lo fija.
#
# 2.000 deja el peor caso —4 × 2.000 = 8.000 parámetros del insert de opciones,
# más los de sus etiquetas y los del delete— con un factor de ocho de margen, y
# ya amortiza por completo lo que el lote vino a arreglar: 17.000 opciones
# pasan de 68.000 sentencias a 27. Medido, subir de ahí no compra tiempo (2.000
# y 10.000 dan lo mismo) y sí cuesta memoria: 136 MB de RSS contra 190 MB.
# La cota de memoria por construcción es la misma propiedad que
# `upsert_records` sostiene para los productos.
OPTION_BATCH_SIZE = 2_000

# El máximo de parámetros de una sentencia en el protocolo de Postgres. Vive
# acá nombrado para que la prueba que verifica el margen no repita el número
# suelto.
POSTGRES_MAX_BIND_PARAMS = 65_535


def upsert_options(
    session: Session,
    tenant_id: int,
    options: list[dict],
    *,
    sync_generation: int,
) -> int:
    """Escribe una PÁGINA de opciones con sus etiquetas en tres sentencias.

    Cada entrada de `options` es `{"attribute_code", "option_id", "labels"}`,
    con `labels` indexado por store view de Magento; 0 es la etiqueta admin.
    Las etiquetas se reemplazan por completo: son un reflejo, no un histórico.

    Por qué en lote (medido al verificar M3 a escala): de a una opción esto
    eran CUATRO sentencias por opción —insert, select del id, delete de las
    etiquetas, insert de las etiquetas— y la pasada de atributos de la
    instancia de referencia, con 50.535 opciones, tardaba 68 s con la primera
    página llevándose ~50 s. Es el mismo defecto que H3 midió y arregló para
    los productos: el coste dominante no es Postgres sino COMPILAR el SQL en
    SQLAlchemy, una vez por opción. Acá se compila una vez por página.

    Tres sentencias, y el orden importa:

    1. `INSERT ... ON CONFLICT DO UPDATE` del sello, con `RETURNING`. Es
       `DO UPDATE` y no `DO NOTHING` por dos razones que se juntan: una opción
       que ya existía y que esta pasada volvió a ver está VIVA y necesita el
       sello nuevo o el barrido de su propia pasada se la lleva; y `DO NOTHING`
       no devuelve fila en el conflicto, así que el `RETURNING` —que es lo que
       reemplaza al `select` por opción— se quedaría sin los ids de todo lo que
       ya existía.
    2. Un `DELETE` de las etiquetas de TODAS las filas del lote.
    3. Un `INSERT` multi-fila con las etiquetas de todas ellas.

    Se deduplica por `(attribute_code, option_id)` conservando la última, por
    la misma razón que `upsert_records`: Postgres rechaza un
    `ON CONFLICT DO UPDATE` que afecte la misma fila dos veces en la misma
    sentencia, y el bucle anterior aplicaba las repetidas en orden dejando
    ganar a la última. El cambio de forma no cambia el resultado.

    `sync_generation` es OBLIGATORIO y sin default: es el sello que salva a
    estas opciones del barrido de su propia pasada, y una opción barrida se
    lleva sus etiquetas, que son el único registro de que "Negro" y "Preto"
    son la misma `option_id`.
    """
    if not options:
        return 0

    deduplicated: dict[tuple[str, int], dict] = {}
    for option in options:
        deduplicated[(option["attribute_code"], option["option_id"])] = option
    everything = list(deduplicated.values())

    written = 0
    for start in range(0, len(everything), OPTION_BATCH_SIZE):
        written += _write_option_batch(
            session,
            tenant_id,
            everything[start : start + OPTION_BATCH_SIZE],
            sync_generation,
        )
    return written


def _write_option_batch(
    session: Session, tenant_id: int, wanted: list[dict], sync_generation: int
) -> int:
    """Un lote de `upsert_options`: sus tres sentencias. Ver ese docstring."""
    stmt = insert(AttributeOption).values(
        [
            {
                "tenant_id": tenant_id,
                "attribute_code": option["attribute_code"],
                "magento_option_id": option["option_id"],
                "sync_generation": sync_generation,
            }
            for option in wanted
        ]
    )
    rows = session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "attribute_code", "magento_option_id"],
            set_={"sync_generation": stmt.excluded.sync_generation},
        ).returning(
            AttributeOption.id,
            AttributeOption.attribute_code,
            AttributeOption.magento_option_id,
        )
    ).all()
    row_ids = {(code, option_id): row_id for row_id, code, option_id in rows}

    session.execute(
        delete(AttributeOptionLabel).where(
            AttributeOptionLabel.option_row_id.in_(list(row_ids.values()))
        )
    )
    labels = [
        {
            "option_row_id": row_ids[(option["attribute_code"], option["option_id"])],
            "store_view_magento_id": store_id,
            "label": label,
        }
        for option in wanted
        for store_id, label in option["labels"].items()
    ]
    if labels:
        session.execute(insert(AttributeOptionLabel).values(labels))
    session.flush()
    return len(wanted)


def option_labels(
    session: Session, tenant_id: int, attribute_code: str, option_id: int
) -> dict[int, str]:
    rows = session.execute(
        select(AttributeOptionLabel.store_view_magento_id, AttributeOptionLabel.label)
        .join(AttributeOption, AttributeOption.id == AttributeOptionLabel.option_row_id)
        .where(
            AttributeOption.tenant_id == tenant_id,
            AttributeOption.attribute_code == attribute_code,
            AttributeOption.magento_option_id == option_id,
        )
    ).all()
    return {store_id: label for store_id, label in rows}


def distinct_option_ids(session: Session, tenant_id: int, attribute_code: str) -> list[int]:
    return list(
        session.scalars(
            select(AttributeOption.magento_option_id)
            .where(
                AttributeOption.tenant_id == tenant_id,
                AttributeOption.attribute_code == attribute_code,
            )
            .order_by(AttributeOption.magento_option_id)
        ).all()
    )


def declared_scopes(session: Session, tenant_id: int) -> dict[str, str]:
    """`{codigo_atributo: "global"|"website"|"store"}` para este tenant.

    Es lo que `resolve_scope` necesita para distinguir un override de WEBSITE
    de uno de tienda: Magento persiste los dos como filas EAV por store view, y
    desde `catalog_product_entity_*` son indistinguibles.

    Un mapa vacío (nadie corrió `sync_attributes` todavía) no es un error: deja
    la procedencia de cada override en DESCONOCIDO, que es la verdad.
    """
    return dict(
        session.execute(
            select(Attribute.code, Attribute.declared_scope).where(
                Attribute.tenant_id == tenant_id
            )
        ).all()
    )
