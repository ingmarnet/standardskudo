"""El resumen legible de una pasada.

Lo que hace útil este informe no es cuántos subtipos encontró, sino cuántos NO
y por qué: un set sin subtipo porque es homogéneo no necesita nada, y uno sin
subtipo porque todos sus candidatos dejaban grupos de treinta productos es una
petición de más catálogo o de otro umbral. Sin la razón, los dos se ven igual.
"""

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.profile.models import ProfilePartition, ProfileRun


def profile_report(session: Session, run: ProfileRun) -> dict:
    particiones = session.scalars(
        select(ProfilePartition).where(ProfilePartition.run_id == run.id)
    ).all()

    por_set: dict[int | None, list[ProfilePartition]] = {}
    for particion in particiones:
        por_set.setdefault(particion.attribute_set_id, []).append(particion)

    razones: Counter = Counter()
    divisores: Counter = Counter()
    con_subtipo = 0
    sin_set = 0
    for attribute_set_id, grupo in por_set.items():
        if attribute_set_id is None:
            sin_set = sum(p.product_count for p in grupo)
            continue
        razon = grupo[0].decision_reason
        razones[razon] += 1
        if razon == "elegido":
            con_subtipo += 1
            divisores[grupo[0].splitter_key] += 1

    return {
        "run_id": run.id,
        "store_view": run.store_view_magento_id,
        "generacion_espejo": run.mirror_sync_generation,
        "digest": run.digest,
        "productos": run.product_count,
        "sets": sum(1 for k in por_set if k is not None),
        "particiones": len(particiones),
        "con_subtipo": con_subtipo,
        "razones": dict(sorted(razones.items())),
        "divisores": dict(sorted(divisores.items())),
        "sin_attribute_set": sin_set,
        "umbrales": run.thresholds,
    }
