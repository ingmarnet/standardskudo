"""Bitácora inmutable de escrituras de la plataforma."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class AuditLog(Base):
    """Bitácora append-only de toda escritura de la plataforma.

    La inmutabilidad la impone la BASE (un trigger de Postgres rechaza UPDATE y
    DELETE), no una promesa de la capa web: ni un bug ni un `db.execute` a mano
    pueden reescribir el pasado. `tenant_id` es NULL para eventos de plataforma
    (alta de usuario, alta de tenant) que no pertenecen a ningún tenant.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), index=True, nullable=True
    )
    actor_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
