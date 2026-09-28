"""La tabla de evidencia por dato (spec §6.6).

Cada dato inferido o corregido guarda de dónde salió: fuente, fragmento o fila
exacta, fecha de observación, identidad del producto origen y transformación
aplicada. Un `conflict_state`/`conflict_group` marca los desacuerdos entre
fuentes sin resolverlos nunca en silencio.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    # A qué propuesta/dato pertenece esta evidencia. Polimórfico: "rule",
    # "finding" o (futuro S3) "remediation_item".
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    # Qué dato dentro del subject ("cobertura", "rango", "valor", ...).
    datum: Mapped[str] = mapped_column(String(64))
    # Procedencia (§6.6): fuente del dato.
    source: Mapped[str] = mapped_column(String(32))
    # El fragmento o fila exacta de donde salió el valor.
    fragment: Mapped[str] = mapped_column(String(2000))
    # Identidad exacta del producto origen. NULL cuando la fuente es agregada
    # (p. ej. un perfil estadístico) y no hay un único producto.
    product_sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Transformación aplicada: "conversion_unidad", "parseo", "normalizacion", ...
    transformation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # El valor que esta fuente sostiene.
    value: Mapped[dict] = mapped_column(JSON)
    # Conflicto entre fuentes: "sin_conflicto" | "en_conflicto".
    conflict_state: Mapped[str] = mapped_column(String(16), default="sin_conflicto")
    conflict_group: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
