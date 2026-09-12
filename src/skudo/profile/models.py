"""Las tablas del perfil.

Un perfil NO es parte del espejo: el espejo dice qué hay en Magento y el perfil
dice qué se midió sobre eso. Viven en módulos distintos para que una migración
del perfil no pueda tocar por accidente la fidelidad del espejo, que es lo único
que no se puede recalcular sin volver a hablar con el cliente.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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


class ProfileRun(Base):
    """Una pasada del perfilador sobre una store view.

    `mirror_sync_generation` y `thresholds` viajan con la fila porque un perfil
    sin ellos es indiscutible: cuando alguien pregunte por qué una regla salió
    así, la respuesta tiene que estar en la propia fila y no en la memoria de
    quien lanzó el comando.
    """

    __tablename__ = "profile_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)
    mirror_sync_generation: Mapped[int] = mapped_column(BigInteger)
    thresholds: Mapped[dict] = mapped_column(JSON)
    product_count: Mapped[int] = mapped_column(Integer)
    # NULL mientras la pasada no termina. Un digest a medias es peor que ninguno:
    # invita a comparar dos perfiles que no midieron lo mismo.
    digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProfilePartition(Base):
    """Un grupo de productos sobre el que se mide.

    `decision_reason` guarda POR QUÉ esta partición es como es —"sin candidatos",
    "ganancia insuficiente", "grupo pequeño", "elegido"—. Que el perfilador diga
    cuándo NO encontró subtipo vale tanto como cuando lo encuentra: sin eso, un
    set homogéneo y un set que nadie supo dividir son indistinguibles.
    """

    __tablename__ = "profile_partition"
    __table_args__ = (
        UniqueConstraint("run_id", "attribute_set_id", "splitter_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("profile_run.id"), index=True)
    # NULL = la partición de los productos cuyo attribute set el espejo desconoce.
    attribute_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    splitter_kind: Mapped[str] = mapped_column(String(32))
    splitter_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    splitter_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_count: Mapped[int] = mapped_column(Integer)
    ambiguity: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_reason: Mapped[str] = mapped_column(String(64))


class AttributeCoverage(Base):
    """Los cuatro estados de un atributo dentro de una partición.

    Las cuatro columnas son cuatro, y no un total con porcentaje, porque el spec
    prohíbe colapsarlas: un atributo con 900 vacíos y otro con 900 desconocidos
    tienen la misma cobertura y significan cosas opuestas.
    """

    __tablename__ = "profile_attribute_coverage"
    __table_args__ = (UniqueConstraint("partition_id", "attribute_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    partition_id: Mapped[int] = mapped_column(
        ForeignKey("profile_partition.id"), index=True
    )
    attribute_code: Mapped[str] = mapped_column(String(255))
    presente: Mapped[int] = mapped_column(Integer)
    vacio: Mapped[int] = mapped_column(Integer)
    no_aplica: Mapped[int] = mapped_column(Integer)
    desconocido: Mapped[int] = mapped_column(Integer)
    # NULL cuando no hay denominador. NO es 0.0: "no se pudo medir" y "no lo
    # tiene nadie" son el par de estados que este producto existe para no
    # confundir.
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)


class ValueStats(Base):
    """Qué valores toma un atributo dentro de una partición."""

    __tablename__ = "profile_value_stats"
    __table_args__ = (UniqueConstraint("partition_id", "attribute_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    partition_id: Mapped[int] = mapped_column(
        ForeignKey("profile_partition.id"), index=True
    )
    attribute_code: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))
    n_present: Mapped[int] = mapped_column(Integer)
    # Valores que NO se pudieron leer como número sin adivinar. Se cuentan
    # aparte en vez de descartarse: son el insumo del detector de sospecha de
    # conversión de S1c.
    n_ambiguous: Mapped[int] = mapped_column(Integer, default=0)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    p05: Mapped[float | None] = mapped_column(Float, nullable=True)
    p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    distinct_values: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    discriminating_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_values: Mapped[list] = mapped_column(JSON, default=list)
