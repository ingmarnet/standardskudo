"""Registro de evidencia con detección de conflictos entre fuentes.

`record` añade una evidencia y, si otra fuente ya sostenía un valor DISTINTO
para el mismo dato, marca a ambas como `en_conflicto` bajo un grupo común. No
hay precedencia silenciosa: los dos valores sobreviven y el desacuerdo queda
declarado para que lo vea quien aprueba.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.evidence.models import Evidence


def record(
    session: Session,
    *,
    tenant_id: int,
    subject_type: str,
    subject_id: int,
    datum: str,
    source: str,
    fragment: str,
    value: dict,
    product_sku: str | None = None,
    transformation: str | None = None,
    observed_at=None,
) -> Evidence:
    """Añade evidencia para un dato y detecta conflictos. No hace commit."""
    ev = Evidence(
        tenant_id=tenant_id,
        subject_type=subject_type,
        subject_id=subject_id,
        datum=datum,
        source=source,
        fragment=fragment,
        value=value,
        product_sku=product_sku,
        transformation=transformation,
        observed_at=observed_at,
        conflict_state="sin_conflicto",
    )
    session.add(ev)
    session.flush()

    # La comparación de valores es en Python, no en SQL: la semántica exacta de
    # "mismo dato" es igualdad de dict, y el volumen por datum es ínfimo.
    otros = session.scalars(
        select(Evidence).where(
            Evidence.tenant_id == tenant_id,
            Evidence.subject_type == subject_type,
            Evidence.subject_id == subject_id,
            Evidence.datum == datum,
            Evidence.id != ev.id,
        )
    ).all()
    disidentes = [o for o in otros if o.value != value]
    if disidentes:
        grupo = uuid4().hex
        for o in disidentes:
            o.conflict_state = "en_conflicto"
            o.conflict_group = grupo
        ev.conflict_state = "en_conflicto"
        ev.conflict_group = grupo
    return ev


def list_for_subject(
    session: Session, subject_type: str, subject_id: int
) -> list[Evidence]:
    """Toda la evidencia de un subject, en orden de inserción."""
    return list(
        session.scalars(
            select(Evidence)
            .where(
                Evidence.subject_type == subject_type,
                Evidence.subject_id == subject_id,
            )
            .order_by(Evidence.id)
        )
    )


def conflicts_for_subject(
    session: Session, subject_type: str, subject_id: int
) -> list[list[Evidence]]:
    """Los desacuerdos de un subject, agrupados por `conflict_group`."""
    grupos: dict[str, list[Evidence]] = {}
    for f in list_for_subject(session, subject_type, subject_id):
        if f.conflict_group:
            grupos.setdefault(f.conflict_group, []).append(f)
    return list(grupos.values())
