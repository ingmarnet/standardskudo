"""Dependencias de la API: fábrica de sesión de base de datos."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from skudo.config import Settings

_factory = None


def get_session_factory() -> sessionmaker:
    global _factory
    if _factory is None:
        settings = Settings()
        engine = create_engine(str(settings.database_url))
        _factory = sessionmaker(engine)
    return _factory
