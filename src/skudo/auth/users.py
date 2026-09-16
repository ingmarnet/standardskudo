"""Alta, consulta y cambio de contraseña de los usuarios de la plataforma."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.auth.models import ROLES, PlatformUser
from skudo.auth.passwords import hash_password, verify_password


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
