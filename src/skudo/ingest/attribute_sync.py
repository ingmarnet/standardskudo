from pydantic import BaseModel
from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.attributes import upsert_attribute, upsert_option


class AttributeSyncReport(BaseModel):
    pages_fetched: int = 0
    attributes_written: int = 0
    options_written: int = 0


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

    No hay watermark ni barrido de attributes/options: `upsert_attribute` y
    `upsert_option` son upserts idempotentes por (tenant, code) y
    (tenant, attribute_code, option_id), así que reejecutar esta sincronización
    no duplica nada.
    """
    tenant_id = source.tenant_id
    client = source.client
    report = AttributeSyncReport()

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
                )
                report.options_written += 1

    session.commit()
    return report
