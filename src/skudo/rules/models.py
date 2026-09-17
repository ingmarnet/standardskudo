"""Las tablas de S1b: reglas, su historial, sus snapshots, el piso externo y el
mapa de conceptos.

Una regla es una FILA, no código (spec §5). Vive y muere por transiciones que
`transitions.py` gobierna; esta capa sólo declara la forma. Ninguna de estas
tablas produce un hallazgo: reglas + espejo → hallazgos es S1c.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class Rule(Base):
    __tablename__ = "rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    # A qué partición se aplica. `scope_key` es texto porque un scope puede ser
    # un id de set ("4"), un valor de subtipo, un id de categoría o "*": una sola
    # columna que los admite a todos, con `scope_kind` diciendo cómo leerla.
    scope_kind: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(255))
    # NULL = vale para todas las store views. Una regla inferida SIEMPRE nace con
    # store view: el perfil es por store view y un 90% en PY y un 12% en BR son
    # dos hechos. Consolidarlas es curación, no inferencia.
    store_view_magento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    axis: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    definition: Mapped[dict] = mapped_column(JSON)
    # Derivada de la evidencia, NUNCA escrita a mano (spec §5).
    confidence: Mapped[float] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer)
    # Los productos de la partición que NO cumplen la regla, con su evidencia.
    # Es lo que `inspect` muestra: lo que la regla marcaría.
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), index=True)
    # NULL mientras S1c no lo mida. No es 0.0: "no medido" y "cero falsos
    # positivos" son cosas distintas.
    false_positive_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), index=True)
    # El perfil que la generó (NULL para piso_externo y curada). Es lo que hace
    # auditable la reproducibilidad: una regla inferida sabe de qué medición sale.
    profile_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("profile_run.id"), nullable=True, index=True
    )
    # El último snapshot con el que se puntuó. Lo escribe S1c.
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RuleVersion(Base):
    """El historial de una regla: cada transición, quién y por qué."""

    __tablename__ = "rule_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id"), index=True)
    # NULL en el alta.
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16))
    # Quién: email del curador, "sistema" (degradación por FP) o "inferencia".
    actor: Mapped[str] = mapped_column(String(320))
    motivo: Mapped[str] = mapped_column(String(1024), default="")
    # La definición EN EL MOMENTO de la transición: un ajuste queda auditable.
    definition_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RulesetSnapshot(Base):
    """El conjunto de reglas vigentes en un instante, para puntuar reproducible.

    S1b lo crea; S1c lo consume. Guarda los ids de las reglas activas, no las
    reglas: la regla puede seguir cambiando, el snapshot es lo que se usó.
    """

    __tablename__ = "ruleset_snapshot"
    __table_args__ = (
        UniqueConstraint("tenant_id", "store_view_magento_id", "version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    rule_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GoogleFloor(Base):
    """Requisitos de Google Shopping por grupo de la taxonomía.

    De REFERENCIA, no por tenant: los requisitos de Google son iguales para
    todos. Se puebla con un seed curado. Un grupo cubre un rango de ids de la
    taxonomía; el universal tiene min/max NULL.
    """

    __tablename__ = "google_floor"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_group: Mapped[str] = mapped_column(String(255))
    google_category_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_category_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_attribute: Mapped[str] = mapped_column(String(64))
    requirement: Mapped[str] = mapped_column(String(16))  # required | recommended
    axis: Mapped[int] = mapped_column(Integer)
    applicability: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(512), default="")


class ConceptMap(Base):
    """Qué atributo del espejo implementa cada concepto canónico. Por tenant.

    Un canónico puede mapear a varios atributos (`color` y `colour`) y un
    atributo servir a varios conceptos, así que la clave es la terna completa.
    """

    __tablename__ = "concept_map"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical", "attribute_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    canonical: Mapped[str] = mapped_column(String(64), index=True)
    attribute_code: Mapped[str] = mapped_column(String(255))
    relation: Mapped[str] = mapped_column(String(16))  # equivalente | sinonimo | unidad_de
    confidence: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(16))  # inferida | curada
