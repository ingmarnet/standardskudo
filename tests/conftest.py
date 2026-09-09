import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session

from alembic import command

DEFAULT_TEST_URL = "postgresql+psycopg://skudo:skudo@localhost:55432/skudo_test"


def require_test_database(url: str) -> str:
    """Se niega a devolver una URL que no apunte a una base de tests.

    El fixture de esquema ejecuta `DROP SCHEMA public CASCADE`. Sin esta guarda,
    una `SKUDO_TEST_DATABASE_URL` mal tecleada (o heredada del entorno de
    producción por un runner) destruye la base a la que apunte. El sufijo
    `_test` en el nombre de la base es la convención que se exige.
    """
    parsed = make_url(url)
    name = parsed.database
    if not name or not name.endswith("_test"):
        safe = parsed.render_as_string(hide_password=True)
        raise RuntimeError(
            "SKUDO_TEST_DATABASE_URL apunta a la base "
            f"{name!r}, cuyo nombre no termina en '_test'.\n"
            f"URL recibida: {safe}\n"
            "El fixture de tests ejecuta 'DROP SCHEMA public CASCADE' contra esa "
            "base, así que se aborta antes de tocarla. Apunta la variable a una "
            f"base cuyo nombre termine en '_test' (por defecto: {DEFAULT_TEST_URL})."
        )
    return url


TEST_URL = require_test_database(
    os.environ.get("SKUDO_TEST_DATABASE_URL", DEFAULT_TEST_URL)
)


@pytest.fixture(scope="session")
def migrated_engine():
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    os.environ["SKUDO_DATABASE_URL"] = TEST_URL
    command.upgrade(Config("alembic.ini"), "head")
    return engine


@pytest.fixture
def db_session(migrated_engine):
    """Sesión con rollback al final: cada test queda aislado."""
    conn = migrated_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()
