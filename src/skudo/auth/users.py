"""Alta, consulta y cambio de contraseña de los usuarios de la plataforma."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from skudo.auth.models import ROLES, PlatformUser, PlatformUserTenantAccess
from skudo.auth.passwords import hash_password, verify_password
from skudo.mirror.models import Tenant


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(session: Session, email: str, password: str, role: str) -> PlatformUser:
    """Crea un usuario. El rol se valida contra el vocabulario cerrado.

    Nunca recibe un hash ya calculado: quien llama pasa la contraseña y este
    módulo decide cómo se guarda. Si el hash entrara por parámetro, el día que
    cambie el algoritmo habría dos lugares que lo saben.
    """
    if role not in ROLES:
        raise ValueError(f"rol desconocido: {role!r}. Los válidos son {', '.join(ROLES)}")
    user = PlatformUser(
        email=normalize_email(email), password_hash=hash_password(password), role=role
    )
    session.add(user)
    session.flush()
    return user


def get_user(session: Session, email: str) -> PlatformUser | None:
    return session.scalar(
        select(PlatformUser).where(PlatformUser.email == normalize_email(email))
    )


def set_password(session: Session, email: str, password: str) -> bool:
    user = get_user(session, email)
    if user is None:
        return False
    user.password_hash = hash_password(password)
    session.flush()
    return True


def authenticate(session: Session, email: str, password: str) -> PlatformUser | None:
    """Devuelve el usuario si la contraseña es correcta Y la cuenta está activa.

    Un usuario desactivado falla como si la contraseña fuera incorrecta: la
    respuesta no distingue 'no existe' de 'está deshabilitado' de 'te
    equivocaste', porque esa distinción es un enumerador de cuentas.
    """
    user = get_user(session, email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def list_users(session: Session) -> list[PlatformUser]:
    return list(session.scalars(select(PlatformUser).order_by(PlatformUser.email)))


def tenant_ids_for_user(session: Session, user_id: int) -> list[int]:
    """Devuelve los tenants explícitamente asignados al usuario.

    Lista vacía significa "sin alcance explícito". El backend web lo interpreta
    como compatibilidad legado: no restringe tenants hasta que se asigna al
    menos uno.
    """
    return sorted(
        session.scalars(
            select(PlatformUserTenantAccess.tenant_id)
            .where(PlatformUserTenantAccess.user_id == user_id)
            .order_by(PlatformUserTenantAccess.tenant_id)
        ).all()
    )


def set_tenant_access(session: Session, user: PlatformUser, tenant_ids: list[int]) -> None:
    """Reemplaza el alcance explícito de tenants de un usuario.

    Valida que todos los ids existan antes de escribir, para que el panel no
    guarde permisos fantasma por una selección stale.
    """
    unique_ids = sorted({int(tid) for tid in tenant_ids})
    if unique_ids:
        existing = set(
            session.scalars(select(Tenant.id).where(Tenant.id.in_(unique_ids))).all()
        )
        missing = [tid for tid in unique_ids if tid not in existing]
        if missing:
            raise ValueError(f"tenant inexistente: {missing[0]}")

    session.execute(
        delete(PlatformUserTenantAccess).where(PlatformUserTenantAccess.user_id == user.id)
    )
    for tenant_id in unique_ids:
        session.add(PlatformUserTenantAccess(user_id=user.id, tenant_id=tenant_id))
    session.flush()
