from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(512))
    # Solo el NOMBRE de la variable de entorno. El token nunca vive en la base.
    token_env_var: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EnvironmentSnapshot(Base):
    """Historial de lo que la sonda encontró. Sirve para detectar que el
    Magento del cliente cambió de versión o activó Staging bajo nuestros pies."""

    __tablename__ = "environment_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    edition: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    product_entity_key: Mapped[str] = mapped_column(String(16))
    staging_enabled: Mapped[bool] = mapped_column(Boolean)
    msi_enabled: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Website(Base):
    __tablename__ = "website"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))


class StoreGroup(Base):
    __tablename__ = "store_group"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    website_magento_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    root_category_id: Mapped[int] = mapped_column(Integer)


class StoreView(Base):
    __tablename__ = "store_view"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    group_magento_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean)
    locale: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8))
