import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command

TEST_URL = os.environ.get(
    "SKUDO_TEST_DATABASE_URL", "postgresql+psycopg://skudo:skudo@localhost:55432/skudo"
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
