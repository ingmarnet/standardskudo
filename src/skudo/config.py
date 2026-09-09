import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del ingestor. Los secretos nunca viven en la base."""

    model_config = SettingsConfigDict(env_prefix="SKUDO_")

    database_url: str

    def tenant_token(self, tenant_code: str) -> str:
        var = f"SKUDO_TENANT_{tenant_code.upper()}_TOKEN"
        try:
            return os.environ[var]
        except KeyError as exc:
            raise KeyError(f"falta la variable de entorno {var}") from exc
