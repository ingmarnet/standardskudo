"""El ciclo diario, sobre TODOS los tenants.

Por qué existe este módulo. Hasta acá cada comando operaba sobre un tenant y
había que nombrarlo: `--tenant renovapadel`. El modelo de datos era multitenant
—cada fila lleva su `tenant_id` y hay tests que lo defienden— pero la
**operación** no lo era, y un temporizador diario habría terminado con el
nombre de un cliente escrito adentro. Una plataforma que hay que arrancar una
vez por cliente no es una plataforma: es una herramienta que se repite.

La propiedad que lo hace multitenant de verdad no es recorrer la lista, es
**aislar el fallo**: el Magento de un cliente caído, un token vencido o un
catálogo que cambió de forma no pueden impedir que los demás se procesen. Por
eso cada tenant corre dentro de su propio `try`, con su propia transacción, y
su fallo viaja en el resultado en lugar de cortar la pasada.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.config import tenant_token
from skudo.findings.run import detect_store_view, findings_report
from skudo.ingest.delta_sync import delta_sync
from skudo.ingest.reconcile import reconcile
from skudo.ingest.source import TenantSource
from skudo.mirror.models import ProductRecord, Tenant
from skudo.report.html import render as render_report
from skudo.score.run import score_run
from skudo.score.trend import comparar_ultima


@dataclass
class ResultadoTenant:
    code: str
    ok: bool = True
    pasos: dict = field(default_factory=dict)
    error: str | None = None
    # El paso en el que se cortó. Sin esto, un tenant fallido dice "falló" y no
    # dónde, que es la diferencia entre revisar el token y revisar la red.
    paso_fallido: str | None = None


@dataclass
class ResultadoCiclo:
    empezado: str
    tenants: list[ResultadoTenant] = field(default_factory=list)

    @property
    def fallidos(self) -> list[str]:
        return [t.code for t in self.tenants if not t.ok]

    def resumen(self) -> dict:
        return {
            "empezado": self.empezado,
            "tenants": len(self.tenants),
            "con_error": self.fallidos,
            "detalle": {
                t.code: (
                    {"ok": True, **t.pasos}
                    if t.ok
                    else {"ok": False, "paso": t.paso_fallido, "error": t.error}
                )
                for t in self.tenants
            },
        }


def _store_views(session: Session, tenant: Tenant) -> list[int]:
    """Las store views que este tenant ya tiene espejadas."""
    return sorted(
        s
        for (s,) in session.execute(
            select(ProductRecord.store_view_magento_id)
            .where(ProductRecord.tenant_id == tenant.id)
            .distinct()
        )
    )


def ciclo_de_un_tenant(
    session: Session,
    tenant: Tenant,
    *,
    transport=None,
    detectar: bool = True,
    informes_en: Path | None = None,
    resultado: ResultadoTenant | None = None,
) -> ResultadoTenant:
    """Delta, reconciliación y detección para un tenant. Sin capturar errores.

    La captura vive en `ciclo_de_todos`: esta función falla ruidosamente para
    que un test pueda comprobar el fallo, y la de arriba decide qué hacer con
    él. Mezclarlas haría imposible probar el aislamiento.
    """
    # El llamador puede pasar el resultado para conservar la REFERENCIA: si
    # esto explota a mitad de camino, quien captura necesita saber en qué paso
    # se cortó, y un objeto creado acá adentro se pierde con la excepción.
    resultado = resultado if resultado is not None else ResultadoTenant(code=tenant.code)

    token = tenant_token(tenant)
    source = TenantSource.from_tenant(tenant, token, transport=transport)
    stores = _store_views(session, tenant)
    resultado.pasos["store_views"] = stores

    resultado.paso_fallido = "delta-sync"
    resultado.pasos["delta"] = delta_sync(session, source, stores).model_dump()

    resultado.paso_fallido = "reconcile"
    deriva = []
    for store in stores:
        informe = reconcile(session, source, store)
        if informe.needs_full_sync or not informe.content_matches:
            deriva.append(store)
    resultado.pasos["store_views_con_deriva"] = deriva

    if detectar:
        resultado.paso_fallido = "findings"
        hallazgos, informes = {}, []
        notas = {}
        for store in stores:
            run = detect_store_view(session, tenant.id, store)
            hallazgos[str(store)] = findings_report(session, run)["hallazgos_totales"]
            salud = score_run(session, run)
            notas[str(store)] = {
                "salud": salud.salud, "grado": salud.grado,
                "criticos": salud.criticos, "distribucion": salud.distribucion,
            }
            if informes_en is not None:
                # El nombre lleva el código del tenant y la store view: el
                # directorio es de la plataforma, no de un cliente, y dos
                # tenants no pueden pisarse el informe.
                informes_en.mkdir(parents=True, exist_ok=True)
                destino = informes_en / f"{tenant.code}-{store}.html"
                destino.write_text(
                    render_report(session, run, tienda=tenant.name or tenant.code),
                    encoding="utf-8",
                )
                informes.append(str(destino))
        resultado.pasos["hallazgos"] = hallazgos
        resultado.pasos["salud"] = notas
        tendencias = {}
        for store in stores:
            comp = comparar_ultima(session, tenant.id, store)
            if comp is not None:
                tendencias[str(store)] = comp.resumen()
        if tendencias:
            resultado.pasos["tendencia"] = tendencias
        if informes:
            resultado.pasos["informes"] = informes

    resultado.paso_fallido = None
    return resultado


def ciclo_de_todos(
    session_factory: Callable[[], Session],
    *,
    transport=None,
    detectar: bool = True,
    informes_en: Path | None = None,
    codigos: list[str] | None = None,
) -> ResultadoCiclo:
    """El ciclo para todos los tenants registrados, cada uno aislado del resto.

    Cada tenant usa una SESIÓN PROPIA. No es prolijidad: con una sesión
    compartida, el fallo de un tenant deja la transacción en estado inválido y
    los siguientes revientan al primer `flush` sin haber hecho nada mal. El
    aislamiento tiene que llegar hasta la transacción o no es aislamiento.
    """
    ciclo = ResultadoCiclo(empezado=datetime.now(UTC).isoformat())

    with session_factory() as session:
        consulta = select(Tenant).order_by(Tenant.code)
        if codigos:
            consulta = consulta.where(Tenant.code.in_(codigos))
        tenants = [(t.id, t.code) for t in session.scalars(consulta)]

    for tenant_id, code in tenants:
        resultado = ResultadoTenant(code=code, paso_fallido="token")
        try:
            with session_factory() as session:
                tenant = session.get(Tenant, tenant_id)
                ciclo_de_un_tenant(
                    session, tenant, transport=transport, detectar=detectar,
                    informes_en=informes_en, resultado=resultado,
                )
                session.commit()
        except KeyError as exc:
            # Sólo es el token si todavía estábamos en ese paso. Capturar
            # KeyError a secas hacía que CUALQUIER clave ausente —una del
            # payload del módulo, por ejemplo— se reportara como «falta la
            # variable de entorno», mandando a revisar credenciales que estaban
            # perfectas. El paso, y no el tipo de excepción, es lo que dice qué
            # pasó.
            resultado.ok = False
            resultado.error = (
                exc.args[0]
                if resultado.paso_fallido == "token"
                else f"KeyError: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 — aislar el fallo ES el punto
            resultado.ok = False
            resultado.error = f"{type(exc).__name__}: {exc}"[:400]
        ciclo.tenants.append(resultado)

    return ciclo


def codigos_registrados(session: Session) -> list[str]:
    return [t.code for t in session.scalars(select(Tenant).order_by(Tenant.code))]


def falta_el_token(tenant: Tenant) -> bool:
    return tenant.token_env_var not in os.environ
