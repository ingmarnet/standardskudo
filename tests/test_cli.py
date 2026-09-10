"""H3 — El punto de entrada del ingestor.

Antes de esto, `main()` existía SOLO en el arnés de aceptación: no había alta
de tenants ni forma de correr una ingesta, así que el comando que verifica el
criterio "espejo de 200k SKUs × 2 store views sincronizado" presuponía un
espejo que nada permitía poblar.

Estas pruebas ejercitan `skudo.cli.main` de verdad —su parseo, sus códigos de
salida, su manejo de errores y sus escrituras en el espejo—, con el transporte
HTTP como único asiento de prueba. Lo que vigilan, además de que cada comando
haga algo:

- Los códigos de salida son los de la convención y NO se solapan: un tenant
  desconocido (2) no puede confundirse con un argumento mal escrito (64) ni
  con un token ausente (3).
- El token no viaja por la línea de comandos y no se imprime NUNCA, ni en el
  reporte de `status`, ni al dar de alta el tenant, ni en un error.
- El token se lee de la variable que la FILA del tenant nombra.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from skudo_testing import checksums_payload, skudo_response
from sqlalchemy import select, text

from skudo import cli
from skudo.exit_codes import (
    EXIT_CONFIGURATION,
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_UNKNOWN_TENANT,
    EXIT_USAGE,
)
from skudo.ingest.reconcile import partition_of
from skudo.mirror.models import ProductRecord, StoreView, Tenant

FIXTURES = Path(__file__).parent / "fixtures"

TOKEN = "el-token-secreto-que-no-debe-aparecer"
TOKEN_VAR = "SKUDO_TENANT_DEMO_TOKEN"

SKU = "SKU-A"


def _item(sku: str, updated_at: str) -> dict:
    return {
        "sku": sku,
        "mpn": None,
        "model": None,
        "gtin": None,
        "variant_key": None,
        "attribute_set_id": 4,
        "type_id": "simple",
        "global_values": {"name": sku},
        "store_values": {},
        "website_ids": [1],
        "category_ids": [7],
        "updated_at": updated_at,
    }


class FakeMagento:
    """La instancia vista por el CLI, con lo justo para cada comando."""

    def __init__(self, skus: tuple[str, ...] = (SKU,), updated_at: str = "2026-09-01 00:00:00"):
        self.skus = skus
        self.updated_at = updated_at
        self.authorizations: list[str] = []

    def transport(self) -> httpx.MockTransport:
        environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            self.authorizations.append(request.headers.get("authorization", ""))
            path = request.url.path
            if path.endswith("/environment"):
                return skudo_response(environment)
            if path.endswith("/products"):
                return skudo_response(
                    {
                        "items": [_item(sku, self.updated_at) for sku in self.skus],
                        "next_cursor": None,
                    }
                )
            if path.endswith("/products-by-sku"):
                asked = json.loads(request.read().decode())["skus"]
                return skudo_response(
                    {
                        "items": [
                            _item(sku, self.updated_at)
                            for sku in asked
                            if sku in self.skus
                        ]
                    }
                )
            if path.endswith("/attributes"):
                return skudo_response(
                    {
                        "items": [
                            {"code": "name", "label": "Nombre", "frontend_input": "text",
                             "declared_scope": "store", "is_filterable": False,
                             "is_required": True, "attribute_set_ids": [4], "options": []}
                        ],
                        "next_cursor": None,
                    }
                )
            if path.endswith("/categories"):
                return skudo_response(
                    {
                        "items": [
                            {"category_id": 7, "path": [1, 2, 7], "default_name": "Casa",
                             "store_states": [
                                 {"store_id": 1, "is_active": True, "name": "Casa"},
                             ]}
                        ],
                        "next_cursor": None,
                    }
                )
            if path.endswith("/signals"):
                return skudo_response({"items": []})
            if path.endswith("/deltas"):
                return skudo_response({"items": [], "last_change_id": None})
            if path.endswith("/checksums"):
                payload = checksums_payload(
                    list(self.skus),
                    updated_at=datetime.strptime(
                        self.updated_at, "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=UTC),
                )
                requested = request.url.params.get("partitions")
                wanted = requested.split(",") if requested else []
                payload["partition_skus"] = [
                    {
                        "partition": partition,
                        "skus": sorted(s for s in self.skus if partition_of(s) == partition),
                    }
                    for partition in wanted
                ]
                return skudo_response(payload)
            return httpx.Response(404)

        return httpx.MockTransport(handler)


@pytest.fixture
def cli_db(migrated_engine, monkeypatch):
    """El CLI abre su PROPIA sesión y confirma de verdad, así que estas pruebas
    no pueden usar el `db_session` de rollback: usan la base de tests y la
    dejan limpia al terminar.

    El `TRUNCATE` no toca `alembic_version` —el esquema se migra una vez por
    sesión de pytest— y es seguro porque `conftest.require_test_database` ya se
    negó a apuntar a una base cuyo nombre no termine en `_test`.
    """
    # `str(engine.url)` enmascara la contraseña con '***': hay que renderizarla
    # explícitamente o el CLI no puede conectarse.
    monkeypatch.setenv(
        "SKUDO_DATABASE_URL", migrated_engine.url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv(TOKEN_VAR, TOKEN)
    yield migrated_engine
    with migrated_engine.begin() as conn:
        tables = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
        ]
        if tables:
            conn.execute(
                text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
            )


def _run(argv, magento: FakeMagento | None = None) -> int:
    return cli.main(argv, transport=magento.transport() if magento else None)


def _register(base_url: str = "https://demo.test") -> int:
    return _run(["register-tenant", "--code", "demo", "--base-url", base_url])


def test_register_tenant_creates_the_row_without_ever_taking_a_token(cli_db, capsys):
    assert _register() == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "demo"
    assert payload["token_env_var"] == TOKEN_VAR
    assert payload["token_env_var_is_set"] is True
    assert payload["created"] is True

    with cli_db.connect() as conn:
        from sqlalchemy.orm import Session

        with Session(bind=conn) as session:
            tenant = session.scalar(select(Tenant).where(Tenant.code == "demo"))
            assert tenant.base_url == "https://demo.test"
            assert tenant.token_env_var == TOKEN_VAR


def test_no_command_accepts_a_token_on_the_command_line(cli_db):
    """La garantía es la ausencia de la opción: mientras exista un `--token`,
    pasarlo es una invocación válida y queda en el historial del shell y en la
    tabla de procesos, donde cualquier usuario lo lee con un `ps`."""
    parser = cli.build_parser()
    actions = [parser]
    # Los subparsers viven en la acción de subcomandos.
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            actions.extend(action.choices.values())

    options = {
        option
        for command in actions
        for action in command._actions
        for option in action.option_strings
    }
    assert "--token" not in options
    assert not any("token" in option and option != "--token-env-var" for option in options)


def test_registering_twice_updates_the_row_instead_of_failing(cli_db, capsys):
    _register()
    capsys.readouterr()

    assert _register("https://otra.test") == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["created"] is False
    assert payload["base_url"] == "https://otra.test"


def test_an_unknown_tenant_exits_with_its_own_code(cli_db, capsys):
    assert _run(["status", "--tenant", "fantasma"]) == EXIT_UNKNOWN_TENANT
    assert "tenant desconocido" in capsys.readouterr().err


def test_a_usage_error_does_not_collide_with_the_unknown_tenant_code(cli_db):
    """`argparse` sale con 2, que acá significa "tenant desconocido". Si no se
    separaran, un `--stores` mal escrito y un tenant inexistente serían
    indistinguibles para el que automatiza."""
    with pytest.raises(SystemExit) as raised:
        _run(["full-sync", "--tenant", "demo", "--stores", "no-son-numeros"])

    assert raised.value.code == EXIT_USAGE
    assert EXIT_USAGE != EXIT_UNKNOWN_TENANT


def test_an_unknown_command_is_a_usage_error(cli_db):
    with pytest.raises(SystemExit) as raised:
        _run(["hacer-magia"])

    assert raised.value.code == EXIT_USAGE


def test_a_missing_token_variable_is_a_configuration_error_and_names_it(
    cli_db, capsys, monkeypatch
):
    _register()
    capsys.readouterr()
    monkeypatch.delenv(TOKEN_VAR)

    assert _run(["probe", "--tenant", "demo"], FakeMagento()) == EXIT_CONFIGURATION

    err = capsys.readouterr().err
    assert TOKEN_VAR in err
    assert TOKEN not in err


def test_a_missing_database_url_is_a_configuration_error(monkeypatch, capsys):
    monkeypatch.delenv("SKUDO_DATABASE_URL", raising=False)

    assert cli.main(["status", "--tenant", "demo"]) == EXIT_CONFIGURATION
    assert "SKUDO_DATABASE_URL" in capsys.readouterr().err


def test_probe_mirrors_the_topology_and_sends_the_token_from_the_row(cli_db, capsys):
    _register()
    capsys.readouterr()
    magento = FakeMagento()

    assert _run(["probe", "--tenant", "demo"], magento) == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["edition"]
    assert magento.authorizations == [f"Bearer {TOKEN}"]

    from sqlalchemy.orm import Session

    with Session(cli_db) as session:
        assert session.scalars(select(StoreView.magento_id)).all()


def test_full_sync_populates_the_mirror_and_reports_its_generation(cli_db, capsys):
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    capsys.readouterr()

    assert _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento()) == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["records_written"] == 1
    assert payload["generation"] > 0
    assert payload["store_views_completed"] == [1]

    from sqlalchemy.orm import Session

    with Session(cli_db) as session:
        assert session.scalar(
            select(ProductRecord.sku).where(ProductRecord.store_view_magento_id == 1)
        ) == SKU


def test_without_stores_the_mirrored_store_views_are_used(cli_db, capsys):
    """El barrido de la pasada completa es POR store view: operar sobre un
    subconjunto silencioso dejaría tiendas sin actualizar sin que nada lo
    dijera. Sin `--stores`, se usan las que la sonda espejó — las dos de la
    fixture, no sólo la primera."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    capsys.readouterr()

    assert _run(["full-sync", "--tenant", "demo"], FakeMagento()) == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["store_views_completed"] == [1, 3]


def test_without_stores_and_without_topology_the_argument_is_required(cli_db, capsys):
    """Adivinar acá es peor que fallar: una lista incompleta de store views
    hace que la pasada barra sólo algunas y las demás queden rancias."""
    _register()
    capsys.readouterr()

    assert _run(["full-sync", "--tenant", "demo"], FakeMagento()) == EXIT_USAGE
    assert "no tiene store views espejadas" in capsys.readouterr().err


def test_the_other_syncs_have_entry_points_too(cli_db, capsys):
    """`sync_attributes`, `sync_categories`, `sync_signals` y `delta_sync`
    existían sin ningún camino de producción: eran alcanzables sólo desde los
    tests."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    capsys.readouterr()

    for argv, key in (
        (["attributes", "--tenant", "demo"], "attributes_written"),
        (["categories", "--tenant", "demo", "--stores", "1"], "categories_written"),
        (["signals", "--tenant", "demo", "--stores", "1"], "store_views_read"),
        (["delta-sync", "--tenant", "demo", "--stores", "1"], "watermark"),
    ):
        assert _run(argv, FakeMagento()) == EXIT_OK, argv
        payload = json.loads(capsys.readouterr().out)
        assert key in payload, argv


def test_reconcile_exits_nonzero_when_the_mirror_drifts(cli_db, capsys):
    """Un comando de verificación que sale 0 con deriva no sirve en un cron."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    capsys.readouterr()
    # Espejo vacío contra una instancia con un producto: deriva de conjunto.

    assert (
        _run(["reconcile", "--tenant", "demo", "--stores", "1"], FakeMagento())
        == EXIT_FAILURE
    )
    assert "deriva de CONJUNTO" in capsys.readouterr().err


def test_reconcile_exits_zero_when_the_mirror_agrees(cli_db, capsys):
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento())
    capsys.readouterr()

    assert (
        _run(["reconcile", "--tenant", "demo", "--stores", "1"], FakeMagento())
        == EXIT_OK
    )


def test_reconcile_names_the_repair_command_for_a_content_drift(cli_db, capsys):
    """La detección tiene que llevar al remedio dirigido: el mensaje trae las
    particiones y el comando que las repara."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento())
    capsys.readouterr()
    # La instancia mueve el `updated_at` del mismo SKU: el conjunto no cambia.
    movido = FakeMagento(updated_at="2026-09-09 12:00:00")

    assert (
        _run(["reconcile", "--tenant", "demo", "--stores", "1"], movido) == EXIT_FAILURE
    )

    err = capsys.readouterr().err
    assert "deriva de CONTENIDO" in err
    assert f"--partitions {partition_of(SKU)}" in err
    assert "--tenant demo" in err


def test_repair_from_reconcile_closes_a_content_drift(cli_db, capsys):
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento())
    movido = FakeMagento(updated_at="2026-09-09 12:00:00")
    capsys.readouterr()

    assert (
        _run(["repair", "--tenant", "demo", "--store", "1", "--from-reconcile"], movido)
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["partitions"] == [partition_of(SKU)]
    assert payload["records_written"] == 1

    assert _run(["reconcile", "--tenant", "demo", "--stores", "1"], movido) == EXIT_OK


def test_repair_refuses_to_pretend_it_can_close_a_set_drift(cli_db, capsys):
    """La reparación dirigida no puede cerrar una deriva de CONJUNTO: falta o
    sobra un producto y no se sabe qué más. Decirlo y salir con fallo es mejor
    que reparar particiones y dejar creer que quedó sincronizado."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    capsys.readouterr()

    assert (
        _run(["repair", "--tenant", "demo", "--store", "1", "--from-reconcile"],
             FakeMagento())
        == EXIT_FAILURE
    )
    assert "deriva es de CONJUNTO" in capsys.readouterr().err


def test_status_says_whether_the_token_variable_is_set_and_never_its_value(
    cli_db, capsys
):
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento())
    capsys.readouterr()

    assert _run(["status", "--tenant", "demo"]) == EXIT_OK

    out = capsys.readouterr().out
    assert TOKEN not in out
    payload = json.loads(out)
    assert payload["tenant"]["token_env_var"] == TOKEN_VAR
    assert payload["tenant"]["token_env_var_is_set"] is True
    assert payload["product_records_by_store_view"] == {"1": 1}
    assert payload["full_sync"][0]["pass_complete"] is True
    assert payload["full_sync"][0]["resumable"] is False
    assert payload["environment"]["version"]


def test_status_shows_a_pass_left_half_way_as_resumable(cli_db, capsys):
    """Lo que un operador necesita saber tras un corte: si la próxima pasada
    continúa esta generación o empieza de cero."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    capsys.readouterr()

    class CortaEnLaSegundaPagina(FakeMagento):
        def transport(self):
            inner = super().transport()

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/products") and not request.url.params.get(
                    "cursor"
                ):
                    return skudo_response(
                        {"items": [_item(SKU, self.updated_at)], "next_cursor": "p1"}
                    )
                if request.url.path.endswith("/products"):
                    raise httpx.ConnectError("corte deliberado")
                return inner.handle_request(request)

            return httpx.MockTransport(handler)

    assert (
        _run(["full-sync", "--tenant", "demo", "--stores", "1"], CortaEnLaSegundaPagina())
        == EXIT_FAILURE
    )
    capsys.readouterr()

    _run(["status", "--tenant", "demo"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["full_sync"][0]["pass_complete"] is False
    assert payload["full_sync"][0]["resumable"] is True
    assert payload["full_sync"][0]["next_cursor"] == "p1"


def test_a_module_error_exits_with_failure_and_names_the_cause(cli_db, capsys):
    """Una traza de 40 líneas en un cron no dice más que la causa, y el tipo
    distingue un fallo del módulo de uno del espejo."""
    _register()
    capsys.readouterr()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "algo se rompió del otro lado"})

    assert cli.main(["probe", "--tenant", "demo"],
                    transport=httpx.MockTransport(handler)) == EXIT_FAILURE
    err = capsys.readouterr().err
    assert "MagentoApiError" in err
    assert "algo se rompió del otro lado" in err


def test_accept_runs_the_s0_criteria_and_fails_when_one_does(cli_db, capsys):
    """El arnés de aceptación deja de presuponer un espejo que nada podía
    poblar: se puede poblar con este mismo CLI y verificar con él."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento())
    capsys.readouterr()

    assert _run(["accept", "--tenant", "demo", "--stores", "1"], FakeMagento()) == EXIT_FAILURE

    out = capsys.readouterr().out
    assert "espejo_sincronizado" in out
    # El criterio 1 sí pasa: el espejo que este CLI acaba de poblar coincide.
    assert "[OK ] espejo_sincronizado" in out
