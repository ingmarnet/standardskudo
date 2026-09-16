"""Dónde vive la nota.

Dos tablas con formas distintas a propósito:

- `product_score` tiene UNA fila por producto, que se reescribe en cada pasada.
  Es el estado actual, como el grado de Akeneo al lado de cada producto. No
  guarda historia: 457.000 filas cada media hora serían 22 millones por día
  para responder una pregunta que nadie hace.
- `catalog_score` tiene una fila por PASADA. Es pequeña, crece despacio, y es lo
  que permite contestar la única pregunta que el spec llama decisiva: ¿el
  catálogo mejora o empeora? Ninguna de las dos cosas la responde la otra.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class ProductScore(Base):
    __tablename__ = "product_score"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", "store_view_magento_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    sku: Mapped[str] = mapped_column(String(255), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)

    puntaje: Mapped[int] = mapped_column(SmallInteger)
    grado: Mapped[str] = mapped_column(String(1), index=True)
    # Aparte del puntaje, no dentro: un producto puede tener buena nota y aun
    # así no poder venderse. Mezclarlos escondería justo eso.
    critico: Mapped[bool] = mapped_column(Boolean, index=True)
    # De dónde sale el número. Es lo que Semrush no publica y acá es la razón
    # de ser de la columna: un catálogo que saca C puede preguntar por qué.
    deducciones: Mapped[list] = mapped_column(JSON)

    run_id: Mapped[int] = mapped_column(ForeignKey("finding_run.id"), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CatalogScore(Base):
    __tablename__ = "catalog_score"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("finding_run.id"), unique=True, index=True
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)

    salud: Mapped[int] = mapped_column(SmallInteger)
    grado: Mapped[str] = mapped_column(String(1))
    productos: Mapped[int] = mapped_column(Integer)
    criticos: Mapped[int] = mapped_column(Integer)
    distribucion: Mapped[dict] = mapped_column(JSON)
    por_eje: Mapped[dict] = mapped_column(JSON)
    medido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
