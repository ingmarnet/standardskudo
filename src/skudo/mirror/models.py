from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
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


class AttributeSet(Base):
    __tablename__ = "attribute_set"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))


class Attribute(Base):
    __tablename__ = "attribute"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    code: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(255))
    frontend_input: Mapped[str] = mapped_column(String(64))
    # 'global' | 'website' | 'store', tal como Magento lo declara.
    declared_scope: Mapped[str] = mapped_column(String(16))
    is_filterable: Mapped[bool] = mapped_column(Boolean)
    is_required: Mapped[bool] = mapped_column(Boolean)
    attribute_set_ids: Mapped[list] = mapped_column(JSON)


class AttributeOption(Base):
    """La identidad de una opción es su option_id de Magento, jamás su etiqueta."""

    __tablename__ = "attribute_option"
    __table_args__ = (UniqueConstraint("tenant_id", "attribute_code", "magento_option_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    attribute_code: Mapped[str] = mapped_column(String(255), index=True)
    magento_option_id: Mapped[int] = mapped_column(Integer)


class AttributeOptionLabel(Base):
    __tablename__ = "attribute_option_label"
    __table_args__ = (UniqueConstraint("option_row_id", "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    option_row_id: Mapped[int] = mapped_column(ForeignKey("attribute_option.id"), index=True)
    # 0 = etiqueta por defecto (admin), N = etiqueta de esa store view.
    store_view_magento_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(512))


class ProductRecord(Base):
    """El objeto central del sistema: (tenant, producto, store view).

    Nunca (tenant, producto). Un producto tiene un registro por store view
    porque su calidad puede ser distinta en PY y en BR.
    """

    __tablename__ = "product_record"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)

    # Identidad desglosada, toda como texto: 0074 != 74, ABC-123/B != ABC-123.
    sku: Mapped[str] = mapped_column(String(255), index=True)
    mpn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    variant_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Clasificación del producto en Magento. `attribute_set_id` es lo que
    # permite distinguir "el atributo aplica y está vacío" (defecto) de "el
    # atributo no pertenece al set de este producto" (no_aplica). NULL significa
    # que la sonda no lo informó: un 0 sería un id de set creíble y falso.
    attribute_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    attributes: Mapped[dict] = mapped_column(JSON)
    # {codigo_atributo: "global"|"store"} — de dónde salió cada valor efectivo.
    scope_provenance: Mapped[dict] = mapped_column(JSON)

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    # Sello de la última pasada completa que tocó esta fila. Al terminar la
    # pasada de una store view, lo que no lleve el sello de esa pasada es una
    # fila que el origen ya no ofrece y se barre. 0 = ninguna pasada completa la
    # ha tocado todavía (la escribió el camino incremental, o es preexistente).
    sync_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    magento_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mirrored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    # Ruta completa de ids desde la raíz. Permite decidir el árbol sin recursión.
    path: Mapped[list] = mapped_column(JSON)
    default_name: Mapped[str] = mapped_column(String(512))


class CategoryStoreState(Base):
    """Nombre y actividad de la categoría EN una store view. Esto sí tiene scope."""

    __tablename__ = "category_store_state"
    __table_args__ = (UniqueConstraint("tenant_id", "category_magento_id",
                                       "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    category_magento_id: Mapped[int] = mapped_column(Integer, index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    name: Mapped[str] = mapped_column(String(512))


class ProductCategoryAssignment(Base):
    """Asignación producto-categoría: GLOBAL, sin store view.

    En el core de Magento `catalog_category_product` no tiene store_id. El efecto
    por tienda se deriva (ver derive_category_effect), nunca se guarda aquí.
    """

    __tablename__ = "product_category_assignment"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", "category_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    sku: Mapped[str] = mapped_column(String(255), index=True)
    category_magento_id: Mapped[int] = mapped_column(Integer, index=True)


class SyncWatermark(Base):
    """Último change_id consumido por tenant. Es lo que hace incremental la sync."""

    __tablename__ = "sync_watermark"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    last_change_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductSignal(Base):
    """Señales comerciales por (tenant, sku, store view).

    `margin` y `salable_qty` admiten NULL a propósito: 'desconocido' no es 'cero'.
    """

    __tablename__ = "product_signal"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    sku: Mapped[str] = mapped_column(String(255), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)

    units_sold: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    salable_qty: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    physical_qty: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    uses_msi: Mapped[bool] = mapped_column(Boolean, default=False)
    margin: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    search_demand: Mapped[int] = mapped_column(Integer, default=0)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
