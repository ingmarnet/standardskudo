"""Los usuarios de la plataforma.

Un usuario NO pertenece a un tenant. Es al revés: la plataforma es nuestra y
los tenants son catálogos que administramos, así que el usuario vive arriba y
su alcance se expresa en su rol. Cuando S7 traiga los roles POR tenant
—operador de un cliente, aprobador de otro— serán una tabla de pertenencia
aparte, y esta no cambia.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base

# Los cuatro roles de la sección 9 del spec, más el que los administra. El
# vocabulario es cerrado a propósito: un rol escrito a mano con una errata es
# un usuario sin permisos que nadie sabe por qué no entra.
SUPERADMIN = "superadmin"
ROLES = (SUPERADMIN, "administrador", "aprobador", "operador", "lector")


class PlatformUser(Base):
    __tablename__ = "platform_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Normalizado a minúsculas al escribir: `Ingmar@x.com` y `ingmar@x.com` son
    # la misma persona, y dos filas para una persona son dos permisos que nadie
    # revoca junto.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # NULL mientras no haya entrado nunca. Un cero o una fecha de relleno
    # dirían "entró al principio del tiempo", que es distinto de "nunca entró".
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PlatformUserTenantAccess(Base):
    """Alcance de un usuario sobre tenants concretos.

    Si un usuario no tiene filas acá, se conserva el comportamiento legado:
    puede ver todos los tenants según su rol global. Apenas tiene una fila, su
    alcance queda limitado explícitamente a esos tenants.
    """

    __tablename__ = "platform_user_tenant_access"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("platform_user.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
