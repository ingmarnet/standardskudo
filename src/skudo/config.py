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


def token_dir() -> str:
    """Directorio donde el onboarding autoservicio persiste el token de cada
    tenant (un archivo por código). No vive en la base ni en el entorno."""
    return os.environ.get("SKUDO_TOKEN_DIR", "/var/lib/skudo/tokens")


def token_file_path(tenant_code: str) -> str:
    return os.path.join(token_dir(), tenant_code)


def write_tenant_token(tenant_code: str, token: str) -> str:
    """Persiste el token en disco (nunca en la base). Devuelve la ruta."""
    d = token_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    p = os.path.join(d, tenant_code)
    with open(p, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(p, 0o600)
    return p


def tenant_token(tenant: Tenant) -> str:
    """El token del tenant, en orden de resolución:

    1. Archivo por tenant en `token_dir()` — lo que escribe el onboarding
       autoservicio desde la UI (`POST /api/tenants`).
    2. La variable de entorno que SU fila nombra (`tenant.token_env_var`) —
       retrocompatibilidad con tenants dados de alta a mano (p. ej. nissei).

    El token no viaja nunca por la línea de comandos ni se guarda en la base:
    la fila solo tiene el NOMBRE de la variable. Un argumento de CLI queda en
    el historial del shell y en la tabla de procesos, donde cualquier usuario
    de la máquina lo lee con un `ps`.

    El `KeyError` nombra la variable que falta y NUNCA su valor: el mensaje
    acaba en logs y en trazas.
    """
    p = token_file_path(tenant.code)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    try:
        return os.environ[tenant.token_env_var]
    except KeyError as exc:
        raise KeyError(
            f"falta el token del tenant '{tenant.code}': ni el archivo {p} "
            f"ni la variable de entorno {tenant.token_env_var} están definidos"
        ) from exc
