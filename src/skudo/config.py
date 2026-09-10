import os

from pydantic_settings import BaseSettings, SettingsConfigDict

from skudo.mirror.models import Tenant


class Settings(BaseSettings):
    """Configuración del ingestor. Los secretos nunca viven en la base."""

    model_config = SettingsConfigDict(env_prefix="SKUDO_")

    database_url: str


def default_token_env_var(tenant_code: str) -> str:
    """El nombre que se PROPONE al dar de alta un tenant.

    Es una convención de conveniencia y nada más: la verdad de dónde vive el
    token de un tenant es la columna `tenant.token_env_var` de su fila, y todo
    el que lo lee la usa. Derivarlo del código en el momento de leer —lo que
    hacía el `Settings.tenant_token(code)` anterior— era una SEGUNDA definición
    de la misma cosa, y en cuanto un tenant guardara otro nombre en su fila
    (dos entornos del mismo cliente, una rotación de credencial) el ingestor
    habría buscado en la variable equivocada y reportado "falta el token"
    teniéndolo delante.
    """
    return f"SKUDO_TENANT_{tenant_code.upper()}_TOKEN"


def tenant_token(tenant: Tenant) -> str:
    """El token del tenant, leído de la variable de entorno que SU fila nombra.

    El token no viaja nunca por la línea de comandos ni se guarda en la base:
    la fila solo tiene el NOMBRE de la variable. Un argumento de CLI queda en
    el historial del shell y en la tabla de procesos, donde cualquier usuario
    de la máquina lo lee con un `ps`.

    El `KeyError` nombra la variable que falta y NUNCA su valor: el mensaje
    acaba en logs y en trazas.
    """
    try:
        return os.environ[tenant.token_env_var]
    except KeyError as exc:
        raise KeyError(
            f"falta la variable de entorno {tenant.token_env_var}, que es donde "
            f"la fila del tenant '{tenant.code}' declara que vive su token"
        ) from exc
