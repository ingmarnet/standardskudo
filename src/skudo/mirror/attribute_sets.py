"""Upsert de los nombres de attribute set en el espejo.

El catálogo de sets es chico y de una sola lectura, así que la pasada
reemplaza el conjunto entero del tenant: upsert de los presentes y borrado de
los que el origen ya no ofrece. Sin generaciones ni cursor —no hay volumen que
lo justifique, a diferencia de atributos o productos—.
"""

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import AttributeSet


def upsert_attribute_sets(session: Session, tenant_id: int, rows: list[dict]) -> int:
    """Escribe los sets del origen y borra los del tenant que ya no vienen.

    Devuelve cuántos sets trae el origen (los presentes tras la pasada).
    """
    presentes = {int(r["magento_id"]): str(r["name"]) for r in rows}

    if presentes:
        values = [
            {"tenant_id": tenant_id, "magento_id": mid, "name": name}
            for mid, name in presentes.items()
        ]
        stmt = insert(AttributeSet).values(values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["tenant_id", "magento_id"],
                set_={"name": stmt.excluded.name},
            )
        )

    # Barrido de lo que el origen dejó de ofrecer. Con la lista vacía, se
    # borran todos los del tenant (el origen no tiene ningún set).
    condicion = [AttributeSet.tenant_id == tenant_id]
    if presentes:
        condicion.append(AttributeSet.magento_id.notin_(list(presentes.keys())))
    session.execute(delete(AttributeSet).where(*condicion))

    session.flush()
    return len(presentes)
