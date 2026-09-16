import pytest

from skudo.auth.models import PlatformUser
from skudo.auth.users import (
    authenticate,
    create_user,
    get_user,
    list_users,
    set_password,
)


def test_alta_y_autenticacion(db_session):
    create_user(db_session, "alguien@ejemplo.com", "secreta", "superadmin")
    assert authenticate(db_session, "alguien@ejemplo.com", "secreta") is not None
    assert authenticate(db_session, "alguien@ejemplo.com", "otra") is None


def test_la_contrasena_no_se_guarda_en_claro(db_session):
    create_user(db_session, "a@b.com", "textoplano", "lector")
    fila = db_session.query(PlatformUser).one()
    assert "textoplano" not in fila.password_hash
    assert fila.password_hash.startswith("scrypt$")


def test_el_email_no_distingue_mayusculas(db_session):
    create_user(db_session, "Ingmar@Ejemplo.COM", "x", "administrador")
    assert get_user(db_session, "ingmar@ejemplo.com") is not None
    assert authenticate(db_session, "INGMAR@ejemplo.com", "x") is not None


def test_un_rol_inventado_se_rechaza(db_session):
    """Vocabulario cerrado: un rol con una errata es un usuario sin permisos
    que nadie sabe por qué no entra."""
    with pytest.raises(ValueError, match="rol desconocido"):
        create_user(db_session, "c@d.com", "x", "superadministrador")


def test_un_usuario_desactivado_no_entra(db_session):
    user = create_user(db_session, "e@f.com", "x", "operador")
    user.is_active = False
    db_session.flush()
    assert authenticate(db_session, "e@f.com", "x") is None


def test_cambiar_la_contrasena_invalida_la_anterior(db_session):
    create_user(db_session, "g@h.com", "vieja", "aprobador")
    assert set_password(db_session, "g@h.com", "nueva") is True
    assert authenticate(db_session, "g@h.com", "vieja") is None
    assert authenticate(db_session, "g@h.com", "nueva") is not None


def test_cambiar_la_contrasena_de_alguien_que_no_existe(db_session):
    assert set_password(db_session, "nadie@x.com", "y") is False


def test_no_se_puede_repetir_un_email(db_session):
    from sqlalchemy.exc import IntegrityError

    create_user(db_session, "i@j.com", "x", "lector")
    # `create_user` hace su propio flush, así que la restricción salta acá
    # mismo y no en un flush posterior: el error aparece donde está la causa.
    with pytest.raises(IntegrityError):
        create_user(db_session, "I@J.com", "y", "lector")


def test_listado_ordenado(db_session):
    for e in ["z@x.com", "a@x.com", "m@x.com"]:
        create_user(db_session, e, "x", "lector")
    assert [u.email for u in list_users(db_session)] == ["a@x.com", "m@x.com", "z@x.com"]
