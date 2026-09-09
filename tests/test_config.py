import pytest

from skudo.config import Settings


def test_database_url_comes_from_environment(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    assert Settings().database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_tenant_token_reads_per_tenant_env_var(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("SKUDO_TENANT_NISSEI_TOKEN", "abc123")
    assert Settings().tenant_token("nissei") == "abc123"


def test_missing_tenant_token_is_an_error(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.delenv("SKUDO_TENANT_GHOST_TOKEN", raising=False)
    with pytest.raises(KeyError):
        Settings().tenant_token("ghost")
