"""El fixture de tests hace `DROP SCHEMA public CASCADE`. La guarda es lo único
que separa una variable de entorno mal tecleada de una base real destruida."""

import pytest
from conftest import TEST_URL, require_test_database


def test_a_database_not_suffixed_test_is_refused():
    with pytest.raises(RuntimeError, match="_test"):
        require_test_database("postgresql+psycopg://skudo:skudo@localhost:55432/skudo")


def test_the_error_names_the_variable_and_the_offending_database():
    with pytest.raises(RuntimeError) as exc:
        require_test_database("postgresql+psycopg://u:secreto@h:5432/produccion")

    message = str(exc.value)
    assert "SKUDO_TEST_DATABASE_URL" in message
    assert "produccion" in message
    assert "DROP SCHEMA" in message
    # La contraseña no se filtra al log de un test que falla.
    assert "secreto" not in message


def test_a_url_without_database_name_is_refused():
    with pytest.raises(RuntimeError):
        require_test_database("postgresql+psycopg://u:p@h:5432/")


def test_a_test_database_is_accepted():
    url = "postgresql+psycopg://skudo:skudo@localhost:55432/skudo_test"
    assert require_test_database(url) == url


def test_the_default_test_url_passes_its_own_guard():
    assert require_test_database(TEST_URL) == TEST_URL
