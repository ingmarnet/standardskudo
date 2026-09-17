"""Comparar dos pasadas consecutivas: qué mejoró, qué empeoró, qué apareció.

La forma viene de Semrush, que en cada rastreo compara con el anterior y le
dice al usuario *qué se arregló y qué apareció*. Eso además da gratis la
detección de regresión: si hallazgos nuevos aparecen donde antes no había, algo
empeoró. El spec marca esa detección como obligatoria.

ProductScore no guarda historia (decisión explícita: una fila por producto, no
por pasada), así que la comparación se basa en los hallazgos (Finding), que SÍ
se almacenan por corrida, y en CatalogScore para el agregado.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.findings.models import Finding
from skudo.score.models import CatalogScore


@dataclass(frozen=True)
class CambioHallazgo:
    """Un tipo de hallazgo que creció o encogió entre dos corridas."""
    code: str
    conteo_antes: int
    conteo_ahora: int

    @property
    def delta(self) -> int:
        return self.conteo_ahora - self.conteo_antes


@dataclass(frozen=True)
class CambioProducto:
    """Un producto que ganó o perdió hallazgos entre dos corridas."""
    sku: str
    hallazgos_antes: int
    hallazgos_ahora: int

    @property
    def delta(self) -> int:
        return self.hallazgos_ahora - self.hallazgos_antes


@dataclass
class Comparacion:
    """La diferencia entre dos corridas consecutivas."""
    run_antes_id: int
    run_ahora_id: int
    salud_antes: int
    salud_ahora: int

    # Productos que antes tenían hallazgos y ahora tienen menos (o cero).
    mejoraron: list[CambioProducto] = field(default_factory=list)
    # Productos que ahora tienen más hallazgos que antes.
    empeoraron: list[CambioProducto] = field(default_factory=list)
    # Productos que aparecen en la corrida actual pero no en la anterior
    # (nuevos en el catálogo, o recién publicados).
    nuevos_con_hallazgos: list[str] = field(default_factory=list)
    # Productos que estaban en la corrida anterior pero ya no están.
    desaparecidos: list[str] = field(default_factory=list)

    # A nivel de tipo de hallazgo.
    hallazgos_nuevos: list[CambioHallazgo] = field(default_factory=list)
    hallazgos_resueltos: list[CambioHallazgo] = field(default_factory=list)
    hallazgos_cambiados: list[CambioHallazgo] = field(default_factory=list)

    @property
    def delta_salud(self) -> int:
        return self.salud_ahora - self.salud_antes

    @property
    def hay_regresion(self) -> bool:
        return self.salud_ahora < self.salud_antes

    def resumen(self) -> dict:
        return {
            "salud_antes": self.salud_antes,
            "salud_ahora": self.salud_ahora,
            "delta_salud": self.delta_salud,
            "regresion": self.hay_regresion,
            "mejoraron": len(self.mejoraron),
            "empeoraron": len(self.empeoraron),
            "nuevos_con_hallazgos": len(self.nuevos_con_hallazgos),
            "desaparecidos": len(self.desaparecidos),
            "hallazgos_nuevos": len(self.hallazgos_nuevos),
            "hallazgos_resueltos": len(self.hallazgos_resueltos),
        }


def _conteos_por_codigo(session: Session, run_id: int) -> dict[str, int]:
    """Cuenta hallazgos agrupados por código para una corrida."""
    filas = session.execute(
        select(Finding.code, func.count())
        .where(Finding.run_id == run_id)
        .group_by(Finding.code)
    ).all()
    return {code: n for code, n in filas}


def _hallazgos_por_sku(session: Session, run_id: int) -> dict[str, int]:
    """Cuenta hallazgos por SKU para productos individuales (subject_type='producto')."""
    filas = session.execute(
        select(Finding.subject_key, func.count())
        .where(Finding.run_id == run_id, Finding.subject_type == "producto")
        .group_by(Finding.subject_key)
    ).all()
    return {sku: n for sku, n in filas}


def comparar(session: Session, antes: CatalogScore, ahora: CatalogScore) -> Comparacion:
    """Compara dos corridas del mismo tenant y store view."""
    comp = Comparacion(
        run_antes_id=antes.run_id,
        run_ahora_id=ahora.run_id,
        salud_antes=antes.salud,
        salud_ahora=ahora.salud,
    )

    # --- Comparación por tipo de hallazgo ---
    conteos_antes = _conteos_por_codigo(session, antes.run_id)
    conteos_ahora = _conteos_por_codigo(session, ahora.run_id)

    todos_codes = set(conteos_antes) | set(conteos_ahora)
    for code in sorted(todos_codes):
        ca = conteos_antes.get(code, 0)
        cd = conteos_ahora.get(code, 0)
        if ca == cd:
            continue
        cambio = CambioHallazgo(code, ca, cd)
        if ca == 0:
            comp.hallazgos_nuevos.append(cambio)
        elif cd == 0:
            comp.hallazgos_resueltos.append(cambio)
        else:
            comp.hallazgos_cambiados.append(cambio)

    # --- Comparación por producto individual ---
    por_sku_antes = _hallazgos_por_sku(session, antes.run_id)
    por_sku_ahora = _hallazgos_por_sku(session, ahora.run_id)

    todos_skus = set(por_sku_antes) | set(por_sku_ahora)
    for sku in sorted(todos_skus):
        ha = por_sku_antes.get(sku, 0)
        hd = por_sku_ahora.get(sku, 0)
        if ha == 0 and hd > 0:
            comp.nuevos_con_hallazgos.append(sku)
        elif ha > 0 and hd == 0:
            comp.desaparecidos.append(sku)
        elif ha != hd:
            cambio = CambioProducto(sku, ha, hd)
            if hd < ha:
                comp.mejoraron.append(cambio)
            else:
                comp.empeoraron.append(cambio)

    comp.mejoraron.sort(key=lambda c: c.delta)
    comp.empeoraron.sort(key=lambda c: c.delta, reverse=True)

    return comp


def ultimas_dos(
    session: Session, tenant_id: int, store_view_magento_id: int
) -> tuple[CatalogScore, CatalogScore] | None:
    """Las dos corridas más recientes, o None si no hay al menos dos."""
    filas = session.scalars(
        select(CatalogScore)
        .where(
            CatalogScore.tenant_id == tenant_id,
            CatalogScore.store_view_magento_id == store_view_magento_id,
        )
        .order_by(CatalogScore.medido_en.desc(), CatalogScore.id.desc())
        .limit(2)
    ).all()
    if len(filas) < 2:
        return None
    return filas[1], filas[0]


def comparar_ultima(
    session: Session, tenant_id: int, store_view_magento_id: int
) -> Comparacion | None:
    """Compara las dos últimas corridas. None si no hay historia suficiente."""
    par = ultimas_dos(session, tenant_id, store_view_magento_id)
    if par is None:
        return None
    return comparar(session, par[0], par[1])
