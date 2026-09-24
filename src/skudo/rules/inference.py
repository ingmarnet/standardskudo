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


# --- Filtro de atributos de sistema ---------------------------------------
#
# Calibrado contra el catálogo real (2026-09-17): la primera pasada sobre
# Renovapadel infirió 560 reglas, de las que ~la mitad eran atributos de SISTEMA
# de Magento —presentes al 100 % porque el motor siempre los setea, no porque
# sean señal de calidad— y reglas de rango sobre campos de config
# (`tax_class_id [2,2]`, `status [1,1]`). Inferir obligatoriedad/rango sobre
# ellos es ruido que ensucia la curación y nunca produce un hallazgo útil.
#
# El filtro CORRECTO a largo plazo es `is_user_defined` de Magento (un atributo
# de sistema lo trae en false), pero eso exige exponerlo en el módulo PHP y
# guardarlo en el espejo. Hasta entonces, este denylist pragmático —constante
# del código, editable en el diff como los umbrales— cubre los conocidos.
ATRIBUTOS_DE_SISTEMA = frozenset({
    "status", "visibility", "tax_class_id", "page_layout", "options_container",
    "gift_message_available", "gift_wrapping_available", "gift_wrapping_price",
    "msrp", "msrp_display_actual_price_type", "quantity_and_stock_status",
    "has_options", "required_options", "url_key", "meta_keyword",
    "custom_design", "custom_design_from", "custom_design_to", "custom_layout",
    "custom_layout_update", "custom_layout_update_file", "weight_type",
    "sku_type", "price_type", "price_view", "shipment_type",
})
# Prefijos de familias técnicas y de plugins (medidos en el catálogo real).
PREFIJOS_DE_SISTEMA = ("ts_dimensions_", "aw_arp_", "use_config_", "links_",
                       "samples_", "bundle_", "giftcard_")


def _es_atributo_de_sistema(code: str) -> bool:
    return code in ATRIBUTOS_DE_SISTEMA or code.startswith(PREFIJOS_DE_SISTEMA)


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
        # Atributo de sistema de Magento: siempre presente por el motor, no es
        # señal de calidad. Ver ATRIBUTOS_DE_SISTEMA.
        if _es_atributo_de_sistema(cov.attribute_code):
            continue
        evaluables = cov.presente + cov.vacio
        if cov.coverage is None or evaluables < MIN_EVIDENCIA:
            continue
        if cov.coverage < UMBRAL_MEDIO:
            continue
        # Una obligatoriedad que no marca a nadie (todos lo tienen) no es
        # accionable: no produce hallazgo y sólo ensucia la curación. Si mañana
        # aparece un hueco, la regla se re-infiere del perfil de esa pasada.
        if cov.vacio == 0:
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
        if _es_atributo_de_sistema(s.attribute_code):
            continue
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
        # Un rango degenerado (p05 == p95) es un valor constante, no un rango de
        # plausibilidad: nada cae "fuera" y la regla no marca nunca. Suele ser un
        # campo de config numérico que el denylist no cubrió.
        if s.p05 == s.p95:
            continue
        reglas.append(Rule(
            tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
            store_view_magento_id=run.store_view_magento_id, axis=3, kind="rango",
            definition={"attribute": s.attribute_code, "min": s.p05 if str(s.p05) not in ("inf", "-inf") else 0.0, "max": s.p95 if str(s.p95) not in ("inf", "-inf") else 0.0},
            confidence=1.0 - ratio, evidence_count=s.n_present, exceptions=[],
            status="borrador", origin="inferida", profile_run_id=run.id,
        ))
    return reglas
