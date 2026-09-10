"""Punto de entrada del ingestor: `python -m skudo.cli`.

H3. Hasta acá `main()` existía SOLO en `skudo/acceptance/s0.py`: no había
forma de dar de alta un tenant ni de correr una ingesta, así que el comando de
aceptación presuponía un espejo poblado que nada permitía poblar, y el primer
criterio —"espejo de 200k SKUs × 2 store views sincronizado"— no se podía ni
intentar.

Los códigos de salida son los de `skudo.exit_codes`, que es la misma
convención que ya usaba el arnés de aceptación: 0 éxito, 1 fallo de la
operación, 2 tenant desconocido, 3 configuración incompleta, 64 error de uso.

EL TOKEN. Ningún comando de este CLI acepta un token como argumento y ningún
comando lo imprime. La fila del tenant guarda el NOMBRE de la variable de
entorno y `config.tenant_token()` la lee de ahí. Un token en la línea de
comandos queda en el historial del shell y en la tabla de procesos, donde
cualquier usuario de la máquina lo lee con un `ps`. `status` informa si la
variable está PUESTA, nunca su contenido.
"""

import argparse
import json
import os
import sys

import httpx
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from skudo.acceptance.s0 import run_s0_acceptance
from skudo.config import Settings, default_token_env_var, tenant_token
from skudo.exit_codes import (
    EXIT_CONFIGURATION,
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_UNKNOWN_TENANT,
    EXIT_USAGE,
)
from skudo.ingest.attribute_sync import sync_attributes
from skudo.ingest.category_sync import sync_categories
from skudo.ingest.delta_sync import delta_sync
from skudo.ingest.full_sync import FULL_SYNC_PAGE_SIZE, full_sync
from skudo.ingest.reconcile import reconcile
from skudo.ingest.repair import partitions_needing_repair, repair_partitions
from skudo.ingest.signal_sync import DEFAULT_SIGNAL_WINDOW_DAYS, sync_signals
from skudo.ingest.source import TenantSource
from skudo.mirror.models import (
    EnvironmentSnapshot,
    FullSyncCheckpoint,
    ProductRecord,
    StoreView,
    SyncWatermark,
    Tenant,
)
from skudo.mirror.topology import sync_topology

# Tope de particiones que `repair --from-reconcile` acepta reparar de una vez.
# Por encima de esto, releer partición por partición es releer una fracción
# grande del catálogo por una vía diseñada para una cohorte: la pasada
# completa es más barata Y arregla también la deriva de CONJUNTO, que la
# reparación dirigida no puede cerrar. Se dice, no se hace en silencio.
MAX_PARTITIONS_TO_REPAIR = 32


class UsageError(Exception):
    """Error de USO, para salir con 64 y no con el 2 de `argparse`."""


class _Parser(argparse.ArgumentParser):
    """`argparse` sale con 2; acá 2 significa 'tenant desconocido'."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error de uso: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _stores(value: str) -> list[int]:
    try:
        ids = [int(part) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"'{value}' no es una lista de ids de store view separados por coma"
        ) from exc
    if not ids:
        raise argparse.ArgumentTypeError("la lista de store views está vacía")
    return ids


def _partitions(value: str) -> list[str]:
    tokens = [part.strip() for part in value.split(",") if part.strip()]
    if not tokens:
        raise argparse.ArgumentTypeError("la lista de particiones está vacía")
    return tokens


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="skudo", description="Ingestor y espejo canónico de StandardSkudo")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser(
        "register-tenant",
        help="da de alta (o actualiza) un tenant a partir de su código, su URL "
        "base y el NOMBRE de la variable de entorno con su token",
    )
    register.add_argument("--code", required=True)
    register.add_argument("--base-url", required=True)
    register.add_argument("--name", default=None, help="por defecto, el código")
    register.add_argument(
        "--token-env-var",
        default=None,
        help="nombre de la variable de entorno donde vive el token; por defecto "
        "SKUDO_TENANT_<CODIGO>_TOKEN. NUNCA el token en sí.",
    )

    def tenant_command(name: str, help_text: str, *, stores: bool = False):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--tenant", required=True, help="código del tenant")
        if stores:
            command.add_argument(
                "--stores",
                type=_stores,
                default=None,
                help="ids de store view separados por coma (p.ej. 1,3). Por "
                "defecto, las store views que la sonda ya espejó para el tenant.",
            )
        return command

    tenant_command("probe", "sonda el Magento del tenant y espeja su topología")

    full = tenant_command(
        "full-sync", "carga completa del catálogo, reanudable", stores=True
    )
    full.add_argument("--page-size", type=int, default=FULL_SYNC_PAGE_SIZE)
    full.add_argument(
        "--restart",
        action="store_true",
        help="descarta el punto de reanudación y empieza una generación nueva",
    )

    tenant_command("delta-sync", "aplica los cambios pendientes de la cola", stores=True)
    tenant_command("attributes", "espeja atributos, opciones y etiquetas")
    tenant_command("categories", "espeja categorías y su estado por tienda", stores=True)
    signals = tenant_command(
        "signals", "espeja las señales comerciales por tienda", stores=True
    )
    signals.add_argument("--days", type=int, default=DEFAULT_SIGNAL_WINDOW_DAYS)

    tenant_command("reconcile", "compara el espejo con Magento", stores=True)

    repair = tenant_command(
        "repair", "relee sólo los SKUs de las particiones divergentes"
    )
    repair.add_argument(
        "--store", type=int, required=True,
        help="id de la store view a reparar; la reparación es POR store view "
        "porque el espejo guarda una fila por (producto, store view)",
    )
    group = repair.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--partitions", type=_partitions,
        help="particiones a reparar, separadas por coma (p.ej. 5b,a7), tal "
        "como las nombra `reconcile`",
    )
    group.add_argument(
        "--from-reconcile",
        action="store_true",
        help="reconcilia primero y repara las particiones que reporte",
    )

    tenant_command("status", "estado del espejo, del watermark y de la pasada en curso")
    tenant_command(
        "accept", "verifica los criterios de aceptación de S0 contra el espejo",
        stores=True,
    )

    return parser


def _report(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _resolve_stores(
    session: Session, tenant: Tenant, requested: list[int] | None
) -> list[int]:
    """Las store views sobre las que operar.

    Sin `--stores`, las que la sonda ya espejó para ESTE tenant. No una lista
    fija ni "todas las de la base": el barrido de la pasada completa es por
    store view, así que operar sobre un subconjunto silencioso dejaría tiendas
    sin actualizar sin que nada lo dijera. Si la topología no está espejada
    todavía, se exige el argumento en vez de adivinar.
    """
    if requested is not None:
        return requested
    ids = list(
        session.scalars(
            select(StoreView.magento_id)
            .where(StoreView.tenant_id == tenant.id)
            .order_by(StoreView.magento_id)
        ).all()
    )
    if not ids:
        raise UsageError(
            f"el tenant '{tenant.code}' no tiene store views espejadas todavía: "
            "corré `probe` primero, o pasá --stores explícitamente"
        )
    return ids


def _register_tenant(session: Session, args) -> int:
    existing = session.scalar(select(Tenant).where(Tenant.code == args.code))
    token_env_var = args.token_env_var or default_token_env_var(args.code)
    if existing is None:
        tenant = Tenant(
            code=args.code,
            name=args.name or args.code,
            base_url=args.base_url,
            token_env_var=token_env_var,
        )
        session.add(tenant)
        created = True
    else:
        tenant = existing
        tenant.base_url = args.base_url
        tenant.token_env_var = token_env_var
        if args.name:
            tenant.name = args.name
        created = False
    session.commit()

    _report(
        {
            "tenant_id": tenant.id,
            "code": tenant.code,
            "name": tenant.name,
            "base_url": tenant.base_url,
            "token_env_var": tenant.token_env_var,
            # Si la variable está puesta o no; su VALOR no se imprime nunca.
            "token_env_var_is_set": tenant.token_env_var in os.environ,
            "created": created,
        }
    )
    if tenant.token_env_var not in os.environ:
        print(
            f"aviso: la variable {tenant.token_env_var} no está definida en este "
            "entorno; ninguna ingesta de este tenant podrá leer su token",
            file=sys.stderr,
        )
    return EXIT_OK


def _status(session: Session, tenant: Tenant) -> int:
    counts = dict(
        session.execute(
            select(ProductRecord.store_view_magento_id, func.count())
            .where(ProductRecord.tenant_id == tenant.id)
            .group_by(ProductRecord.store_view_magento_id)
            .order_by(ProductRecord.store_view_magento_id)
        ).all()
    )
    watermark = session.scalar(
        select(SyncWatermark).where(SyncWatermark.tenant_id == tenant.id)
    )
    checkpoints = session.scalars(
        select(FullSyncCheckpoint)
        .where(FullSyncCheckpoint.tenant_id == tenant.id)
        .order_by(FullSyncCheckpoint.store_view_magento_id)
    ).all()
    snapshot = session.scalar(
        select(EnvironmentSnapshot)
        .where(EnvironmentSnapshot.tenant_id == tenant.id)
        .order_by(EnvironmentSnapshot.observed_at.desc())
        .limit(1)
    )

    _report(
        {
            "tenant": {
                "id": tenant.id,
                "code": tenant.code,
                "base_url": tenant.base_url,
                "token_env_var": tenant.token_env_var,
                "token_env_var_is_set": tenant.token_env_var in os.environ,
            },
            "store_views_mirrored": list(
                session.scalars(
                    select(StoreView.magento_id)
                    .where(StoreView.tenant_id == tenant.id)
                    .order_by(StoreView.magento_id)
                ).all()
            ),
            "product_records_by_store_view": counts,
            "watermark": None
            if watermark is None
            else {
                "last_change_id": watermark.last_change_id,
                "last_delta_read_at": watermark.last_delta_read_at,
                "updated_at": watermark.updated_at,
            },
            "full_sync": [
                {
                    "store_view_magento_id": checkpoint.store_view_magento_id,
                    "generation": checkpoint.generation,
                    "pages_done": checkpoint.pages_done,
                    "records_written": checkpoint.records_written,
                    "pass_complete": checkpoint.pass_complete,
                    "swept": checkpoint.swept,
                    # Lo que un operador necesita saber de un vistazo: si esta
                    # store view quedó a medias, la próxima pasada CONTINÚA
                    # esta generación en vez de empezar de cero.
                    "resumable": not (checkpoint.pass_complete and checkpoint.swept),
                    "next_cursor": checkpoint.next_cursor,
                    "updated_at": checkpoint.updated_at,
                }
                for checkpoint in checkpoints
            ],
            "environment": None
            if snapshot is None
            else {
                "edition": snapshot.edition,
                "version": snapshot.version,
                "product_entity_key": snapshot.product_entity_key,
                "staging_enabled": snapshot.staging_enabled,
                "msi_enabled": snapshot.msi_enabled,
                "observed_at": snapshot.observed_at,
            },
        }
    )
    return EXIT_OK


def _reconcile(
    session: Session, source: TenantSource, tenant_code: str, store_ids: list[int]
) -> int:
    drift = {store_id: reconcile(session, source, store_id) for store_id in store_ids}
    _report({store_id: report.model_dump() for store_id, report in drift.items()})

    for store_id, report in drift.items():
        if report.needs_full_sync:
            print(
                f"store {store_id}: deriva de CONJUNTO (magento="
                f"{report.magento_count}, espejo={report.mirror_count}). El "
                "remedio es `full-sync`: no se sabe qué más falta.",
                file=sys.stderr,
            )
        elif not report.content_matches:
            partitions = ",".join(partitions_needing_repair(report))
            print(
                f"store {store_id}: deriva de CONTENIDO en "
                f"{len(report.diverging_partitions)} de {report.partition_count} "
                f"particiones. Remedio dirigido: `skudo repair --tenant "
                f"{tenant_code} --store {store_id} --partitions {partitions}`",
                file=sys.stderr,
            )

    drifted = any(
        report.needs_full_sync or not report.content_matches for report in drift.values()
    )
    return EXIT_FAILURE if drifted else EXIT_OK


def _repair(session: Session, source: TenantSource, args) -> int:
    if args.from_reconcile:
        drift = reconcile(session, source, args.store)
        if drift.needs_full_sync:
            print(
                f"store {args.store}: la deriva es de CONJUNTO (magento="
                f"{drift.magento_count}, espejo={drift.mirror_count}), no de "
                "contenido: falta o sobra un producto y no se sabe qué más. La "
                "reparación dirigida no puede cerrar eso; el remedio es "
                "`full-sync`.",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        partitions = partitions_needing_repair(drift)
        if not partitions:
            # Clave en inglés como el resto de los reportes, que salen de los
            # modelos pydantic: un consumidor no debería tener que saber en
            # qué idioma se escribió cada campo.
            _report(
                {
                    "store_view_magento_id": args.store,
                    "partitions": [],
                    "nothing_to_repair": True,
                }
            )
            return EXIT_OK
        if len(partitions) > MAX_PARTITIONS_TO_REPAIR:
            print(
                f"store {args.store}: divergen {len(partitions)} de "
                f"{drift.partition_count} particiones. Releerlas una por una es "
                "releer una fracción grande del catálogo por una vía pensada "
                "para una cohorte: corré `full-sync`, que además cierra la "
                "deriva de conjunto.",
                file=sys.stderr,
            )
            return EXIT_FAILURE
    else:
        partitions = args.partitions

    report = repair_partitions(session, source, args.store, partitions)
    _report(report.model_dump())
    return EXIT_OK


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    """`transport` es un asiento de prueba y en producción es siempre None.

    Existe para que las pruebas ejerciten ESTE comando —su parseo, sus códigos
    de salida, su manejo de errores— y no una reimplementación suya. Toda la
    verificación sobre HTTP real de la ingesta pasa por este mismo `main` con
    `transport=None`.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except ValidationError:
        print(
            "falta SKUDO_DATABASE_URL: el ingestor no sabe en qué espejo escribir",
            file=sys.stderr,
        )
        return EXIT_CONFIGURATION

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            if args.command == "register-tenant":
                return _register_tenant(session, args)

            tenant = session.scalar(select(Tenant).where(Tenant.code == args.tenant))
            if tenant is None:
                print(f"tenant desconocido: {args.tenant}", file=sys.stderr)
                return EXIT_UNKNOWN_TENANT

            if args.command == "status":
                return _status(session, tenant)

            try:
                token = tenant_token(tenant)
            except KeyError as exc:
                # `exc.args[0]` es el mensaje que `tenant_token` compuso: nombra
                # la variable que falta, nunca un valor.
                print(exc.args[0], file=sys.stderr)
                return EXIT_CONFIGURATION

            store_ids = (
                _resolve_stores(session, tenant, args.stores)
                if hasattr(args, "stores")
                else []
            )

            source = TenantSource.from_tenant(tenant, token, transport=transport)
            try:
                if args.command == "probe":
                    profile = source.client.environment()
                    sync_topology(session, tenant.id, profile)
                    session.commit()
                    _report(profile.model_dump())
                    return EXIT_OK
                if args.command == "full-sync":
                    _report(
                        full_sync(
                            session,
                            source,
                            store_ids,
                            page_size=args.page_size,
                            restart=args.restart,
                        ).model_dump()
                    )
                    return EXIT_OK
                if args.command == "delta-sync":
                    _report(delta_sync(session, source, store_ids).model_dump())
                    return EXIT_OK
                if args.command == "attributes":
                    _report(sync_attributes(session, source).model_dump())
                    return EXIT_OK
                if args.command == "categories":
                    _report(sync_categories(session, source, store_ids).model_dump())
                    return EXIT_OK
                if args.command == "signals":
                    _report(
                        sync_signals(
                            session, source, store_ids, days=args.days
                        ).model_dump()
                    )
                    return EXIT_OK
                if args.command == "reconcile":
                    return _reconcile(session, source, tenant.code, store_ids)
                if args.command == "repair":
                    return _repair(session, source, args)
                if args.command == "accept":
                    results = run_s0_acceptance(session, source, store_ids)
                    for result in results:
                        print(
                            f"[{'OK ' if result.passed else 'FALLA'}] "
                            f"{result.name}: {result.detail}"
                        )
                    return EXIT_OK if all(r.passed for r in results) else EXIT_FAILURE
            finally:
                source.close()
    except UsageError as exc:
        print(f"skudo: error de uso: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001 - la frontera del proceso
        # Una traza de 40 líneas en un cron no dice más que la causa; el tipo
        # se nombra para que un fallo del módulo (MagentoApiError) se distinga
        # de uno del espejo.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    # Inalcanzable: `argparse` exige un subcomando conocido.
    raise AssertionError(f"comando sin despachar: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
