from types import SimpleNamespace

import pytest

from skudo.config import Settings, default_token_env_var, tenant_token


def test_database_url_comes_from_environment(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    assert Settings().database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_the_token_is_read_from_the_variable_the_tenant_row_names(monkeypatch):
    """La fila manda. La versión anterior derivaba el nombre del CÓDIGO del
    tenant, que es una segunda definición de la misma cosa: un tenant con otro
    nombre en su fila —dos entornos del mismo cliente, una credencial rotada—
    hacía buscar en la variable equivocada."""
    monkeypatch.setenv("SKUDO_TOKEN_DE_ESTA_FILA", "abc123")
    tenant = SimpleNamespace(code="nissei", token_env_var="SKUDO_TOKEN_DE_ESTA_FILA")

    assert tenant_token(tenant) == "abc123"


def test_the_conventional_name_is_not_consulted_when_the_row_says_another(monkeypatch):
    """Discrimina contra la implementación anterior: con las DOS variables
    puestas, la que gana tiene que ser la que la fila nombra."""
    monkeypatch.setenv("SKUDO_TENANT_NISSEI_TOKEN", "el-de-la-convencion")
    monkeypatch.setenv("SKUDO_OTRO_NOMBRE", "el-de-la-fila")
    tenant = SimpleNamespace(code="nissei", token_env_var="SKUDO_OTRO_NOMBRE")

    assert tenant_token(tenant) == "el-de-la-fila"


def test_a_missing_token_names_the_variable_and_never_a_value(monkeypatch):
    monkeypatch.delenv("SKUDO_TOKEN_AUSENTE", raising=False)
    tenant = SimpleNamespace(code="ghost", token_env_var="SKUDO_TOKEN_AUSENTE")

    with pytest.raises(KeyError) as raised:
        tenant_token(tenant)

    assert "SKUDO_TOKEN_AUSENTE" in str(raised.value)
    assert "ghost" in str(raised.value)


def test_the_conventional_name_is_only_a_default_for_registration():
    assert default_token_env_var("nissei") == "SKUDO_TENANT_NISSEI_TOKEN"
