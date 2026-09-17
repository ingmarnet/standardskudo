"""Perfil → reglas en borrador. La única entrada es el perfil de S1a.

No lee el espejo: eso lo hace reproducible y barato de re-correr. Si un dato no
está en el perfil, falta en el perfil (cambio de S1a), no se busca por otro lado.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.profile.models import (
    AttributeCoverage,
    ProfilePartition,
    ProfileRun,
    ValueStats,
)
from skudo.rules.models import Rule, RuleVersion

# --- Umbrales -------------------------------------------------------------
#
# Constantes del código, NO configuración por tenant: un umbral que se afloja
# por config termina aflojado (lección de la válvula de S0 y de los umbrales del
# perfilador). Son una HIPÓTESIS; la calibración contra el catálogo real está en
# la Task 8 y deja su cifra en docs/superpowers/s1b-calibracion.md.

# Cobertura por encima de la cual la obligatoriedad se infiere sin reparos.
UMBRAL_ALTO = 0.90
# Entre este y el alto, se infiere PERO se marca `ambiguo`.
UMBRAL_MEDIO = 0.70
# Productos evaluables mínimos para inferir: con menos, la cobertura es ruido.
MIN_EVIDENCIA = 50
# Proporción de valores no interpretables por encima de la cual un numérico no
# da rango sino sospecha de conversión.
MAX_RATIO_AMBIGUO = 0.10
# Tope de SKUs de excepción que una regla guarda inline (hoy la cobertura
# agregada no trae SKUs; el tope rige cuando S1c los adjunte).
MAX_EXCEPCIONES = 500


def _scope(part: ProfilePartition) -> tuple[str, str]:
    """El scope de una partición: subtype si tiene divisor, si no attribute_set."""
    if part.splitter_value is not None:
        return "subtype", str(part.splitter_value)
    return "attribute_set", str(part.attribute_set_id)


def inferir(session: Session, profile_run_id: int) -> list[Rule]:
    """Genera y persiste las reglas borrador de un ProfileRun. Devuelve las reglas.

    Idempotente por construcción del que llama: re-inferir requiere borrar las
    reglas del run primero (el CLI lo hace). Acá no se deduplica contra lo
    existente porque la inferencia es una función del perfil, no del estado.
    """
    run = session.get(ProfileRun, profile_run_id)
    if run is None or run.finished_at is None:
        raise ValueError(f"perfil {profile_run_id} inexistente o sin terminar")

    particiones = session.scalars(
        select(ProfilePartition)
        .where(ProfilePartition.run_id == profile_run_id)
        .order_by(ProfilePartition.id)
    ).all()

    reglas: list[Rule] = []
    for part in particiones:
        if part.attribute_set_id is None:
            continue  # partición de set desconocido: no se puede inferir
        scope_kind, scope_key = _scope(part)
        reglas.extend(_obligatoriedad(session, run, part, scope_kind, scope_key))
        reglas.extend(_numericas(session, run, part, scope_kind, scope_key))

    for r in reglas:
        session.add(r)
    session.flush()
    for r in reglas:
        session.add(RuleVersion(
            rule_id=r.id, from_status=None, to_status="borrador",
            actor="inferencia", motivo="alta por inferencia",
            definition_snapshot=r.definition,
        ))
    session.flush()
    return reglas


def _obligatoriedad(session, run, part, scope_kind, scope_key) -> list[Rule]:
    coberturas = session.scalars(
        select(AttributeCoverage)
        .where(AttributeCoverage.partition_id == part.id)
        .order_by(AttributeCoverage.attribute_code)  # orden estable = reproducible
    ).all()
    reglas = []
    for cov in coberturas:
        evaluables = cov.presente + cov.vacio
        if cov.coverage is None or evaluables < MIN_EVIDENCIA:
            continue
        if cov.coverage < UMBRAL_MEDIO:
            continue
        definition = {"attribute": cov.attribute_code, "marcaria": cov.vacio}
        if cov.coverage < UMBRAL_ALTO:
            definition["ambiguo"] = True
        reglas.append(Rule(
            tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
            store_view_magento_id=run.store_view_magento_id, axis=3,
            kind="obligatoriedad", definition=definition, confidence=cov.coverage,
            evidence_count=cov.presente, exceptions=[], status="borrador",
            origin="inferida", profile_run_id=run.id,
        ))
    return reglas


def _numericas(session, run, part, scope_kind, scope_key) -> list[Rule]:
    stats = session.scalars(
        select(ValueStats)
        .where(ValueStats.partition_id == part.id, ValueStats.kind == "numerico")
        .order_by(ValueStats.attribute_code)
    ).all()
    reglas = []
    for s in stats:
        if s.n_present < MIN_EVIDENCIA:
            continue
        ratio = (s.n_ambiguous / s.n_present) if s.n_present else 1.0
        if ratio >= MAX_RATIO_AMBIGUO:
            reglas.append(Rule(
                tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
                store_view_magento_id=run.store_view_magento_id, axis=3,
                kind="formato",
                definition={"attribute": s.attribute_code, "sospecha_conversion": True,
                            "ratio_ambiguo": round(ratio, 4)},
                confidence=1.0 - ratio, evidence_count=s.n_present, exceptions=[],
                status="borrador", origin="inferida", profile_run_id=run.id,
            ))
            continue
        if s.p05 is None or s.p95 is None:
            continue
        reglas.append(Rule(
            tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
            store_view_magento_id=run.store_view_magento_id, axis=3, kind="rango",
            definition={"attribute": s.attribute_code, "min": s.p05, "max": s.p95},
            confidence=1.0 - ratio, evidence_count=s.n_present, exceptions=[],
            status="borrador", origin="inferida", profile_run_id=run.id,
        ))
    return reglas
