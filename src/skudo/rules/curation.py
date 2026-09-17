"""La capa de curación: transiciones puras sobre reglas.

Recibe ids y strings, devuelve objetos. Ninguna función lee de argv ni imprime:
el CLI las envuelve y S1d será una vista sobre ellas, no una reimplementación.
La validez de cada transición la decide `transitions`, la única autoridad.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.rules.models import Rule, RulesetSnapshot, RuleVersion
from skudo.rules.transitions import exigir_transicion

# Estados que un snapshot congela: sólo los que puntúan o avisan.
ACTIVOS = ("aceptada", "aviso")
# Por encima de este FP medido, una regla aceptada se degrada sola a aviso.
UMBRAL_FP = 0.10


class ReglaAmbiguaSinConfirmar(Exception):
    """Se intentó aceptar por lote una regla marcada `ambiguo` sin confirmar."""


def _transicionar(session, rule: Rule, to_status: str, actor: str, motivo: str) -> None:
    exigir_transicion(rule.origin, rule.status, to_status)
    from_status = rule.status
    rule.status = to_status
    session.add(RuleVersion(
        rule_id=rule.id, from_status=from_status, to_status=to_status,
        actor=actor, motivo=motivo, definition_snapshot=rule.definition,
    ))
    session.flush()


def aceptar(session: Session, rule_ids, actor: str, confirmar_ambiguo: bool = False) -> None:
    """Acepta reglas por lote. Se planta ante una `ambiguo` sin confirmación."""
    reglas = session.scalars(select(Rule).where(Rule.id.in_(list(rule_ids)))).all()
    for r in reglas:
        if r.definition.get("ambiguo") and not confirmar_ambiguo:
            raise ReglaAmbiguaSinConfirmar(
                f"la regla {r.id} está marcada ambigua; usar confirmar_ambiguo"
            )
    for r in reglas:
        _transicionar(session, r, "aceptada", actor, "aceptada por curación")


def rechazar(session: Session, rule_id: int, actor: str, motivo: str) -> None:
    if not motivo.strip():
        raise ValueError("rechazar exige un motivo")
    r = session.get(Rule, rule_id)
    _transicionar(session, r, "rechazada", actor, motivo)


def acotar(session: Session, rule_id: int, actor: str, motivo: str) -> None:
    """Acota una regla a `aviso`. Es la única salida del piso externo."""
    if not motivo.strip():
        raise ValueError("acotar exige un motivo")
    r = session.get(Rule, rule_id)
    _transicionar(session, r, "aviso", actor, motivo)


def ajustar(session: Session, rule_id: int, actor: str, definition: dict) -> None:
    """Cambia la definición; el historial guarda la nueva para poder ver el cambio."""
    r = session.get(Rule, rule_id)
    r.definition = definition
    session.add(RuleVersion(
        rule_id=r.id, from_status=r.status, to_status=r.status,
        actor=actor, motivo="ajuste de definición", definition_snapshot=definition,
    ))
    session.flush()


def degradar_por_fp(session: Session, rule_id: int) -> bool:
    """Degrada de aceptada a aviso si el FP medido supera el umbral. Automática.

    La dispara S1c, que es quien mide el FP; S1b provee la transición y la
    registra con actor 'sistema' para que quede claro que no fue una persona.
    """
    r = session.get(Rule, rule_id)
    if r.status != "aceptada" or r.false_positive_rate is None:
        return False
    if r.false_positive_rate < UMBRAL_FP:
        return False
    _transicionar(session, r, "aviso", "sistema",
                  f"fp {r.false_positive_rate:.3f} > umbral {UMBRAL_FP}")
    return True


def snapshot(session: Session, tenant_id: int, store_view: int) -> RulesetSnapshot:
    """Congela las reglas activas de una store view en un snapshot con versión nueva."""
    ids = session.scalars(
        select(Rule.id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.status.in_(ACTIVOS),
            (Rule.store_view_magento_id == store_view)
            | (Rule.store_view_magento_id.is_(None)),
        )
        .order_by(Rule.id)
    ).all()
    ultima = session.scalar(
        select(func.coalesce(func.max(RulesetSnapshot.version), 0)).where(
            RulesetSnapshot.tenant_id == tenant_id,
            RulesetSnapshot.store_view_magento_id == store_view,
        )
    )
    snap = RulesetSnapshot(
        tenant_id=tenant_id, store_view_magento_id=store_view,
        version=(ultima or 0) + 1, rule_ids=list(ids),
    )
    session.add(snap)
    session.flush()
    return snap


def inspeccionar(session: Session, rule_id: int) -> dict:
    """Qué marcaría la regla y qué descartaría, para el criterio de aceptación 3."""
    r = session.get(Rule, rule_id)
    return {
        "id": r.id,
        "kind": r.kind,
        "attribute": r.definition.get("attribute"),
        "status": r.status,
        "origin": r.origin,
        "confidence": r.confidence,
        "marcaria": r.definition.get("marcaria", len(r.exceptions)),
        "descartaria": r.evidence_count,
        "exceptions": r.exceptions,
        "definition": r.definition,
    }
