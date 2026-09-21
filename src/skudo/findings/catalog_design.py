"""Eje 11 — diseño del catálogo: hallazgos de CONFIGURACIÓN, no de producto.

A diferencia de los detectores por producto, esto mira el catálogo como un todo:
attribute sets sin uso, y filtros mal puestos. No produce filas `Finding` ni
toca la nota de producto (los hallazgos son de sujeto set/atributo, que el
scoring no atribuye a ningún SKU): se calcula on-demand desde el espejo y el
último perfil, y se muestra en su propia superficie.

Los umbrales son CONSTANTES del código, no configuración por tenant: un umbral
que se afloja por config termina aflojado (misma lección que la inferencia).
"""

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute, AttributeSet, ProductRecord
from skudo.profile.models import (
    AttributeCoverage,
    ProfilePartition,
    ProfileRun,
    ValueStats,
)
from skudo.rules.inference import _es_atributo_de_sistema

# Cobertura por encima de la cual un atributo tiene dato suficiente para ser un
# filtro útil (mismo piso que la obligatoriedad de alta confianza).
UMBRAL_COBERTURA_PERDIDO = 0.90
# Productos evaluables mínimos: con menos, cualquier proporción es ruido.
MIN_EVIDENCIA = 50
# Un valor que domina >= 95 % de la partición: el filtro ofrece una sola opción
# efectiva y no discrimina nada.
MODE_SHARE_INUTIL = 0.95
# El spec exige "una decena de candidatos ordenados por impacto", no un volcado
# de mil filas.
TOPE_CANDIDATOS = 25
# Solo un atributo de valores DISCRETOS puede ser un filtro de navegación por
# facetas. Calibrado contra datos reales (2026-09-21): sin esto, `filtro
# perdido` proponía `name`, `meta_title`, `meta_description` y `weight` —texto
# libre, SEO y magnitudes físicas— como candidatos a filtrable, que es basura.
# Magento arma la navegación por capas sobre select/multiselect.
FILTRABLE_INPUTS = frozenset({"select", "multiselect"})


def sets_muertos(session: Session, tenant_id: int) -> list[dict]:
    """Attribute sets definidos sin un solo producto.

    Definidos = la unión de `Attribute.attribute_set_ids` (de qué sets habla el
    catálogo de atributos). En uso = los `attribute_set_id` que algún producto
    realmente lleva. La diferencia son sets que ensucian la curación y las
    estadísticas sin describir a nadie.
    """
    definidos: set[int] = set()
    for (ids,) in session.execute(
        select(Attribute.attribute_set_ids).where(Attribute.tenant_id == tenant_id)
    ):
        for sid in ids or []:
            definidos.add(int(sid))

    en_uso = {
        int(sid)
        for (sid,) in session.execute(
            select(distinct(ProductRecord.attribute_set_id)).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.attribute_set_id.is_not(None),
            )
        )
    }

    nombres = {
        mid: name
        for mid, name in session.execute(
            select(AttributeSet.magento_id, AttributeSet.name).where(
                AttributeSet.tenant_id == tenant_id
            )
        )
    }

    muertos = sorted(definidos - en_uso)
    return [{"magento_id": sid, "name": nombres.get(sid)} for sid in muertos]


def _atributos(session: Session, tenant_id: int) -> dict[str, dict]:
    """`{attribute_code: {is_filterable, frontend_input}}` para el tenant."""
    return {
        code: {"is_filterable": bool(is_f), "frontend_input": fi}
        for code, is_f, fi in session.execute(
            select(Attribute.code, Attribute.is_filterable, Attribute.frontend_input).where(
                Attribute.tenant_id == tenant_id
            )
        )
    }


def _valor_dominante(top_values) -> str | None:
    """El valor más frecuente de `top_values`, sea `[valor, conteo]` o `valor`."""
    if not top_values:
        return None
    first = top_values[0]
    if isinstance(first, (list, tuple)) and first:
        return str(first[0])
    return str(first)


def filtros_inutiles(
    session: Session, tenant_id: int, profile_run: ProfileRun
) -> list[dict]:
    """Atributos FILTRABLES con un único valor efectivo: un filtro de una sola
    opción, que no discrimina. Del perfil (`ValueStats.mode_share`)."""
    attrs = _atributos(session, tenant_id)
    filas = session.execute(
        select(ValueStats, ProfilePartition.attribute_set_id)
        .join(ProfilePartition, ValueStats.partition_id == ProfilePartition.id)
        .where(ProfilePartition.run_id == profile_run.id)
    ).all()

    out = []
    for vs, set_id in filas:
        code = vs.attribute_code
        info = attrs.get(code)
        if _es_atributo_de_sistema(code) or not info or not info["is_filterable"]:
            continue
        if vs.n_present < MIN_EVIDENCIA:
            continue
        un_solo_valor = vs.distinct_values == 1 or (
            vs.mode_share is not None and vs.mode_share >= MODE_SHARE_INUTIL
        )
        if not un_solo_valor:
            continue
        out.append({
            "attribute": code,
            "attribute_set_id": set_id,
            "mode_share": vs.mode_share,
            "valor_dominante": _valor_dominante(vs.top_values),
            "n_present": vs.n_present,
        })
    out.sort(key=lambda d: (-d["n_present"], d["attribute"]))
    return out[:TOPE_CANDIDATOS]


def filtros_perdidos(
    session: Session, tenant_id: int, profile_run: ProfileRun
) -> list[dict]:
    """Atributos con buen dato y cobertura que NO están marcados filtrables:
    productos que ya existen y no se pueden encontrar al filtrar. CANDIDATO —
    sin señal de demanda (no disponible en el tenant piloto), es una propuesta
    a revisar, no un veredicto. Del perfil (`AttributeCoverage`)."""
    attrs = _atributos(session, tenant_id)

    # Poder discriminante por (partición, atributo): un atributo constante no
    # sería un filtro útil aunque esté 100 % cargado.
    degenerado: dict[tuple[int, str], bool] = {}
    for vs in session.scalars(
        select(ValueStats)
        .join(ProfilePartition, ValueStats.partition_id == ProfilePartition.id)
        .where(ProfilePartition.run_id == profile_run.id)
    ):
        es_deg = vs.distinct_values == 1 or (
            vs.mode_share is not None and vs.mode_share >= MODE_SHARE_INUTIL
        )
        degenerado[(vs.partition_id, vs.attribute_code)] = es_deg

    filas = session.execute(
        select(AttributeCoverage, ProfilePartition.attribute_set_id)
        .join(ProfilePartition, AttributeCoverage.partition_id == ProfilePartition.id)
        .where(ProfilePartition.run_id == profile_run.id)
    ).all()

    out = []
    for cov, set_id in filas:
        code = cov.attribute_code
        info = attrs.get(code)
        if _es_atributo_de_sistema(code) or not info or info["is_filterable"]:
            continue  # ya filtrable, o de sistema, o desconocido
        # Solo valores discretos pueden ser un filtro: name/meta/weight (texto,
        # SEO, físico) tienen buena cobertura pero no son navegación por facetas.
        if info["frontend_input"] not in FILTRABLE_INPUTS:
            continue
        if cov.coverage is None or cov.coverage < UMBRAL_COBERTURA_PERDIDO:
            continue
        if cov.presente < MIN_EVIDENCIA:
            continue
        # Sólo se excluye si el perfil CONFIRMA que es degenerado; si no hay
        # ValueStats para ese par, la ignorancia no lo descarta.
        if degenerado.get((cov.partition_id, code)):
            continue
        out.append({
            "attribute": code,
            "attribute_set_id": set_id,
            "coverage": cov.coverage,
            "presente": cov.presente,
            "candidato": True,
            "nota": "sin señal de demanda",
        })
    out.sort(key=lambda d: (-d["presente"], d["attribute"]))
    return out[:TOPE_CANDIDATOS]
