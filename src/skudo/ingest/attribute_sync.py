from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.ingest.sweep import (
    PASS_ATTRIBUTES,
    next_generation,
    note_page,
    require_complete_pass,
    start_pass,
)
from skudo.mirror.attributes import upsert_attribute, upsert_option
from skudo.mirror.models import Attribute, AttributeOption, AttributeOptionLabel


class AttributeSyncReport(BaseModel):
    # Sello de esta pasada. Se reporta para que desde fuera se pueda verificar
    # con qué generación quedaron selladas las filas.
    generation: int = 0
    pages_fetched: int = 0
    attributes_written: int = 0
    options_written: int = 0
    # M3: lo que el origen dejó de ofrecer y esta pasada barrió.
    attributes_deleted: int = 0
    options_deleted: int = 0
    option_labels_deleted: int = 0


def _labels_by_store_id(labels: object) -> dict[int, str]:
    """Convierte el mapa de etiquetas de JSON a `dict[int, str]`.

    Las claves de un objeto JSON son siempre strings, así que
    `{"0": "Negro", "1": "Negro PY"}` llega tal cual desde `response.json()`.
    `upsert_option` espera `dict[int, str]`; sin esta conversión, las
    store_view_ids quedarían guardadas como texto y cualquier búsqueda
    posterior por id entero no encontraría nada, en silencio.

    Si `labels` llega como lista en vez de objeto, no se coacciona
    posicionalmente: PHP colapsa un array entero-clave con claves consecutivas
    desde cero a una lista JSON (45.801 de 50.545 opciones de este catálogo
    tienen esa forma exacta). Asignar por posición ahí sería asignar la
    etiqueta de un store_id a otro sin que nada lo note. Se prefiere fallar
    ruidosamente.
    """
    if not isinstance(labels, dict):
        raise TypeError(
            "labels debe ser un objeto JSON keyed por store_id, no "
            f"{type(labels).__name__}: {labels!r}. Esto indica que el emisor "
            "perdió el keying por store_id (colapso de PHP de un mapa "
            "entero-clave con claves consecutivas desde cero a una lista)."
        )
    return {int(store_id): label for store_id, label in labels.items()}


def sync_attributes(session: Session, source: TenantSource) -> AttributeSyncReport:
    """Vuelca atributos, opciones y etiquetas por store view en el espejo.

    Recibe un `TenantSource` y no `(client, tenant_id)` sueltos por la misma
    razón que `full_sync`/`delta_sync`: para que el catálogo que se lee y el
    tenant en el que se escribe no puedan desemparejarse.

    BARRIDO (M3). Hasta este cierre esta función documentaba "no hay barrido"
    como fuera de alcance, y una opción que el origen borraba seguía
    pareciendo viva en el espejo para siempre. Eso importa por encima de la
    higiene: la consolidación de S1 decide por `option_id`, así que una opción
    muerta que parece viva es candidata a una corrección que no apuntaría a
    nada, y un atributo muerto que parece filtrable es un hallazgo
    filter-blind fabricado.

    Cada pasada toma una generación, sella con ella lo que escribe, y al
    TERMINAR borra del tenant lo que no la lleve. La precondición vive dentro
    de `_sweep_attributes` y se lee de la base (`require_complete_pass`), así
    que una pasada interrumpida no puede barrer.

    COMMIT POR PÁGINA, y por qué acá no hace falta reanudar: una pasada
    interrumpida deja el espejo parcialmente refrescado y su sello a medias;
    la siguiente NO continúa desde donde quedó sino que recorre otra vez desde
    la primera página con una generación nueva, así que todo lo que el origen
    sigue ofreciendo se vuelve a sellar y sólo lo que de verdad desapareció
    queda sin sello. El cursor persistido que `full_sync` necesita —porque
    releer 228.889 productos cuesta media hora— acá no tendría lector: el
    catálogo de atributos son unos miles de filas.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = AttributeSyncReport()

    generation = next_generation(session)
    report.generation = generation
    pass_row = start_pass(session, tenant_id, PASS_ATTRIBUTES, generation)
    session.commit()

    for page in client.iter_attributes():
        report.pages_fetched += 1
        for item in page["items"]:
            upsert_attribute(
                session,
                tenant_id,
                {
                    "code": item["code"],
                    "label": item["label"],
                    "frontend_input": item["frontend_input"],
                    "declared_scope": item["declared_scope"],
                    "is_filterable": item["is_filterable"],
                    "is_required": item["is_required"],
                    "attribute_set_ids": item["attribute_set_ids"],
                },
                sync_generation=generation,
            )
            report.attributes_written += 1

            for option in item.get("options", []):
                # Una llamada por opción con el mapa COMPLETO de etiquetas:
                # upsert_option reemplaza las etiquetas de la opción por
                # completo, así que una llamada por etiqueta borraría en cada
                # vuelta lo que la vuelta anterior acababa de escribir.
                upsert_option(
                    session,
                    tenant_id,
                    item["code"],
                    option["option_id"],
                    _labels_by_store_id(option["labels"]),
                    sync_generation=generation,
                )
                report.options_written += 1

        # El sello de "vio la última página" se escribe en la MISMA
        # transacción que la última página, no antes: si el commit no llega,
        # la pasada sigue estando a medias y el barrido sigue prohibido.
        note_page(
            session, pass_row, len(page["items"]), is_last=page.get("next_cursor") is None
        )
        session.commit()

    (
        report.attributes_deleted,
        report.options_deleted,
        report.option_labels_deleted,
    ) = _sweep_attributes(session, tenant_id, generation)
    session.commit()
    return report


def _sweep_attributes(
    session: Session, tenant_id: int, generation: int
) -> tuple[int, int, int]:
    """Borra atributos, opciones y etiquetas que esta pasada no selló.

    La precondición se lee de la BASE y no de una variable del llamador
    (`require_complete_pass`): ningún camino puede omitirla, y un barrido
    sobre una pasada a medias —que se llevaría lo que falta por recorrer, con
    sus etiquetas— no es expresable.

    ORDEN: primero las etiquetas de las opciones condenadas y después las
    opciones, porque `attribute_option_label.option_row_id` es una FK sin
    cascada. Y las etiquetas se acotan por el `option_row_id` de OPCIONES DE
    ESTE TENANT: esa tabla no tiene `tenant_id` propio —su alcance es su
    opción— así que ese subselect es todo el aislamiento que hay, y por eso
    hay una prueba de que las etiquetas del otro tenant sobreviven.

    Los atributos se barren con las opciones a propósito: un atributo que el
    origen borró deja de aparecer en la pasada, así que sus opciones tampoco
    se sellan y caen por el mismo filtro. Dejar la fila del atributo haría que
    `declared_scopes` —y el detector filter-blind de S1— vieran un atributo
    filtrable que no existe.
    """
    pass_row = require_complete_pass(session, tenant_id, PASS_ATTRIBUTES, generation)

    stale_options = select(AttributeOption.id).where(
        AttributeOption.tenant_id == tenant_id,
        AttributeOption.sync_generation != generation,
    )
    labels_deleted = session.execute(
        delete(AttributeOptionLabel).where(
            AttributeOptionLabel.option_row_id.in_(stale_options)
        )
    )
    options_deleted = session.execute(
        delete(AttributeOption).where(
            AttributeOption.tenant_id == tenant_id,
            AttributeOption.sync_generation != generation,
        )
    )
    attributes_deleted = session.execute(
        delete(Attribute).where(
            Attribute.tenant_id == tenant_id,
            Attribute.sync_generation != generation,
        )
    )

    pass_row.swept = True
    session.flush()
    return (
        attributes_deleted.rowcount or 0,
        options_deleted.rowcount or 0,
        labels_deleted.rowcount or 0,
    )
