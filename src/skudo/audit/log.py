"""Registro append-only de escrituras de la plataforma."""

from sqlalchemy.orm import Session

from skudo.audit.models import AuditLog


def record(
    session: Session,
    *,
    action: str,
    actor_email: str,
    tenant_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: dict | None = None,
) -> None:
    """Añade una entrada a la bitácora. NO hace commit: se confirma en la MISMA
    transacción que la escritura que audita, para que no pueda existir un
    registro sin efecto ni un efecto sin registro."""
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_email=actor_email,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
