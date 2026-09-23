"""Nombre sin traducir para la store view (Eje 1).

Spec §6.1 eje 1: «nombre sin traducir para la store view». El nombre de un
producto sigue en el idioma de la store view fuente —la de menor `magento_id`,
que en la práctica es la primera creada y la del idioma original— en vez de
traducirse. Necesita los pares de store view y sus locales, que las fichas no
traen, así que lo evalúa `run.py` como detector especial, igual que
filter-blind. Es CANDIDATO: un nombre idéntico puede ser una marca universal
que no se traduce en ningún idioma.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.findings.catalog import (
    CANDIDATO,
    Cobertura,
    Ficha,
    Hallazgo,
    Resultado,
    _publicado,
)
from skudo.mirror.models import ProductRecord, StoreView

CODE = "nombre_sin_traducir"
AXIS = 1


def _idioma(locale: str) -> str:
    # "es_PY" -> "es": el prefijo antes del guion bajo es el idioma.
    return (locale or "").split("_", 1)[0].lower()


def _no_aplica(total: int, motivo: str) -> Resultado:
    return Resultado([], Cobertura(CODE, 0, total, 0, motivo))


def evaluar_nombre_sin_traducir(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    fichas: Sequence[Ficha],
) -> list[Resultado]:
    stores = session.scalars(
        select(StoreView)
        .where(StoreView.tenant_id == tenant_id, StoreView.is_active.is_(True))
        .order_by(StoreView.magento_id)
    ).all()
    if len(stores) < 2:
        return [_no_aplica(len(fichas), "no hay un par de store views con que comparar")]

    fuente = stores[0]  # la de menor magento_id: el idioma original
    actual = next((s for s in stores if s.magento_id == store_view_magento_id), None)
    if actual is None:
        return [_no_aplica(len(fichas), "store view sin fila en el espejo")]
    if actual.magento_id == fuente.magento_id:
        return [_no_aplica(len(fichas), "es la store view fuente: no hay qué traducir")]
    if _idioma(actual.locale) == _idioma(fuente.locale):
        return [_no_aplica(len(fichas), "la store view fuente comparte idioma")]

    nombre_en_fuente: dict[str, str] = {}
    for sku, attrs in session.execute(
        select(ProductRecord.sku, ProductRecord.attributes).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == fuente.magento_id,
        )
    ).all():
        nombre = (attrs or {}).get("name")
        if nombre:
            nombre_en_fuente[sku] = nombre.strip()

    hallazgos: list[Hallazgo] = []
    evaluados = no_aplica = no_evaluado = 0
    for f in fichas:
        if not _publicado(f):
            no_aplica += 1
            continue
        nombre = f.valor("name")
        if not nombre:
            no_evaluado += 1  # sin nombre acá: no hay qué comparar
            continue
        nombre_f = nombre_en_fuente.get(f.sku)
        if nombre_f is None:
            no_evaluado += 1  # no existe en la fuente
            continue
        evaluados += 1
        if nombre == nombre_f:
            hallazgos.append(
                Hallazgo(
                    CODE, AXIS, CANDIDATO, "producto", f.sku,
                    {
                        "nombre": nombre,
                        "store_fuente": fuente.code,
                        "lectura": f"{f.sku} repite el nombre de la store view {fuente.code}",
                    },
                )
            )
    return [
        Resultado(
            hallazgos,
            Cobertura(
                CODE, evaluados, no_aplica, no_evaluado,
                "no publicados, sin nombre, o sin contraparte en la store view fuente",
            ),
        )
    ]
