"""Las tablas de hallazgos.

Un hallazgo no se guarda solo: se guarda junto a la **cobertura** del detector
que lo produjo. Un informe que dice «197 productos sin imagen» sin decir sobre
cuántos se buscó es un número que no se puede auditar, y el spec lo llama
engañoso con todas las letras.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class FindingRun(Base):
    __tablename__ = "finding_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)
    mirror_sync_generation: Mapped[int] = mapped_column(BigInteger)
    product_count: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # La versión del ruleset con la que se evaluaron las reglas de esta pasada.
    # NULL si no había snapshot (sólo corrieron los detectores especiales).
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Finding(Base):
    __tablename__ = "finding"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("finding_run.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    axis: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(16))
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_key: Mapped[str] = mapped_column(String(255))
    evidence: Mapped[dict] = mapped_column(JSON)
    # De qué regla salió el hallazgo. NULL para los detectores especiales, que
    # no nacen de una regla. Es lo que deja al panel decir "esto lo produjo esta
    # regla".
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule.id"), nullable=True, index=True
    )
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DetectorCoverage(Base):
    """Qué pudo mirar cada detector en cada pasada."""

    __tablename__ = "finding_coverage"
    __table_args__ = (UniqueConstraint("run_id", "detector"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("finding_run.id"), index=True)
    detector: Mapped[str] = mapped_column(String(64))
    evaluados: Mapped[int] = mapped_column(Integer)
    no_aplica: Mapped[int] = mapped_column(Integer)
    no_evaluado: Mapped[int] = mapped_column(Integer)
    motivo_no_aplica: Mapped[str] = mapped_column(String(512), default="")
