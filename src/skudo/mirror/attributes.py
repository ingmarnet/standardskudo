from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute, AttributeOption, AttributeOptionLabel


def upsert_attribute(session: Session, tenant_id: int, payload: dict) -> None:
    stmt = insert(Attribute).values(
        tenant_id=tenant_id,
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
            },
        )
    )
    session.flush()


def upsert_option(
    session: Session,
    tenant_id: int,
    attribute_code: str,
    option_id: int,
    labels: dict[int, str],
) -> None:
    """Guarda una opción y todas sus etiquetas por store view.

    `labels` va indexado por store view de Magento; 0 es la etiqueta admin.
    Las etiquetas se reemplazan por completo: son un reflejo, no un histórico.
    """
    stmt = insert(AttributeOption).values(
        tenant_id=tenant_id, attribute_code=attribute_code, magento_option_id=option_id
    )
    session.execute(
        stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "attribute_code", "magento_option_id"]
        )
    )
    session.flush()

    row_id = session.scalar(
        select(AttributeOption.id).where(
            AttributeOption.tenant_id == tenant_id,
            AttributeOption.attribute_code == attribute_code,
            AttributeOption.magento_option_id == option_id,
        )
    )

    session.execute(
        delete(AttributeOptionLabel).where(AttributeOptionLabel.option_row_id == row_id)
    )
    if labels:
        session.execute(
            insert(AttributeOptionLabel).values(
                [
                    {"option_row_id": row_id, "store_view_magento_id": store_id, "label": label}
                    for store_id, label in labels.items()
                ]
            )
        )
    session.flush()


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
