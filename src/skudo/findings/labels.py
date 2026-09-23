"""El arnés de falsos positivos: etiquetar hallazgos y medir la tasa.

El spec §2.2 manda medir antes de razonar: una regla es una hipótesis hasta
que alguien etiqueta su salida y la tasa de FP se calcula sobre eso, no sobre
una intuición. `medir_fp` escribe `Rule.false_positive_rate` y dispara
`degradar_por_fp` cuando la tasa supera el umbral. El arnés no decide qué es
un defecto: mide lo que ya decidió un humano.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.findings.models import Finding, FindingLabel, FindingRun
from skudo.rules.curation import degradar_por_fp
from skudo.rules.models import Rule

ETIQUETAS = ("verdadero", "falso", "no_aplica")


def etiquetar(session: Session, finding_id: int, label: str, actor: str) -> FindingLabel:
    """Etiqueta UN hallazgo. Re-etiquetar sobrescribe la decisión anterior."""
    if label not in ETIQUETAS:
        raise ValueError(f"etiqueta desconocida: {label!r}")
    finding = session.get(Finding, finding_id)
    if finding is None:
        raise ValueError(f"no existe el hallazgo {finding_id}")
    run = session.get(FindingRun, finding.run_id)
    fila = session.scalar(
        select(FindingLabel).where(FindingLabel.finding_id == finding_id)
    )
    if fila is None:
        fila = FindingLabel(
            tenant_id=run.tenant_id, finding_id=finding_id, label=label,
            labeled_by=actor,
        )
    fila.label = label
    fila.labeled_by = actor
    session.add(fila)
    session.flush()
    return fila


def medir_fp(session: Session, tenant_id: int) -> dict:
    """Mide la tasa de FP por regla y por detector, y degrada las reglas que se pasan.

    Devuelve un resumen. Las reglas sin etiquetas quedan con `false_positive_rate`
    intacto (NULL = no medido, que no es lo mismo que cero).
    """
    filas = session.execute(
        select(Finding.rule_id, Finding.code, FindingLabel.label)
        .join(FindingLabel, FindingLabel.finding_id == Finding.id)
        .where(FindingLabel.tenant_id == tenant_id)
    ).all()

    por_regla: dict[int, dict[str, int]] = defaultdict(
        lambda: {"verdaderos": 0, "falsos": 0}
    )
    por_detector: dict[str, dict[str, int]] = defaultdict(
        lambda: {"verdaderos": 0, "falsos": 0}
    )
    for rule_id, code, label in filas:
        if label not in ("verdadero", "falso"):
            continue
        destino = por_regla[rule_id] if rule_id is not None else por_detector[code]
        destino["verdaderos" if label == "verdadero" else "falsos"] += 1

    resumen_reglas: dict[str, dict] = {}
    for rule_id, c in por_regla.items():
        total = c["verdaderos"] + c["falsos"]
        if total == 0:
            continue
        fp = c["falsos"] / total
        regla = session.get(Rule, rule_id)
        if regla is not None:
            regla.false_positive_rate = fp
            degradar_por_fp(session, rule_id)
        resumen_reglas[str(rule_id)] = {
            "verdaderos": c["verdaderos"], "falsos": c["falsos"], "fp": round(fp, 3),
        }
    session.flush()

    resumen_detectores = {
        code: {
            "verdaderos": c["verdaderos"], "falsos": c["falsos"],
            "fp": round(c["falsos"] / (c["verdaderos"] + c["falsos"]), 3),
        }
        for code, c in por_detector.items()
        if c["verdaderos"] + c["falsos"]
    }
    return {"reglas": resumen_reglas, "detectores": resumen_detectores}
