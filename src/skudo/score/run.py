"""Calcular y guardar la nota de una pasada de detección."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.findings.catalog import PUBLICADO
from skudo.findings.models import Finding, FindingRun
from skudo.findings.run import fichas_de
from skudo.score.models import CatalogScore, ProductScore
from skudo.score.scoring import (
    HallazgoDeProducto,
    nota_de_producto,
    salud_de_catalogo,
)

# Filas por sentencia. Postgres admite 65.535 parámetros por sentencia y cada
# fila lleva ocho columnas: el techo real está en 8.191. Se deja en 2.000 por el
# mismo motivo que en el espejo, donde este límite ya provocó un crash que
# ningún test vio y sí encontró la primera pasada a escala.
LOTE = 2000


def _hallazgos_por_sku(session: Session, run: FindingRun) -> dict[str, list]:
    """Atribuye cada hallazgo a los productos que afecta.

    Un hallazgo de grupo —ocho talles del mismo modelo— afecta a sus ocho
    miembros: cada uno de esos productos ESTÁ mal registrado. Por eso el
    detector guarda la lista completa de SKUs y no sólo unos ejemplos.
    """
    por_sku: dict[str, list] = {}
    for f in session.scalars(
        select(Finding).where(Finding.run_id == run.id).order_by(Finding.id)
    ):
        # Las reglas guardan el atributo en evidence.attribute; los detectores
        # especiales de carencia de campo lo guardan en evidence.campo. Ambos
        # alimentan la misma causa, que es lo que funde las dos marcas del
        # mismo atributo (spec §6.4: sin doble penalización).
        causa = (f.evidence or {}).get("attribute") or (f.evidence or {}).get("campo")
        h = HallazgoDeProducto(code=f.code, severity=f.severity, axis=f.axis, causa=causa)
        if f.subject_type == "producto":
            por_sku.setdefault(f.subject_key, []).append(h)
        elif f.subject_type == "grupo":
            for sku in (f.evidence or {}).get("skus", []):
                por_sku.setdefault(sku, []).append(h)
    return por_sku


def score_run(session: Session, run: FindingRun) -> CatalogScore:
    """Puntúa una pasada ya terminada y deja la nota escrita.

    Se puntúan los productos que el detector EVALUÓ, no todos los del espejo:
    darle un 100 a una variante que nadie navega inflaría la salud del catálogo
    con productos que no se miraron. La cobertura del detector ya dice cuántos
    fueron y por qué.
    """
    # Se puntúa a los PUBLICADOS, que son los que los detectores evalúan. Y se
    # decide con la MISMA función que ellos usan, no con una copia del
    # predicado: en Renovapadel hay 2.329 variantes y deshabilitados que ningún
    # detector mira, y darles 100 subiría la salud del catálogo con productos
    # que nadie evaluó. Es exactamente el error del denominador que este
    # proyecto existe para no cometer, y la primera versión de esta función lo
    # cometía.
    fichas = fichas_de(session, run.tenant_id, run.store_view_magento_id)
    publicados = [f.sku for f in fichas if f.estado == PUBLICADO]
    por_sku = _hallazgos_por_sku(session, run)
    notas = [nota_de_producto(sku, por_sku.get(sku, [])) for sku in publicados]

    filas = [
        {
            "tenant_id": run.tenant_id,
            "sku": n.sku,
            "store_view_magento_id": run.store_view_magento_id,
            "puntaje": n.puntaje,
            "grado": n.grado,
            "critico": n.critico,
            "deducciones": n.deducciones,
            "run_id": run.id,
        }
        for n in notas
    ]
    for i in range(0, len(filas), LOTE):
        stmt = insert(ProductScore).values(filas[i : i + LOTE])
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["tenant_id", "sku", "store_view_magento_id"],
                set_={
                    "puntaje": stmt.excluded.puntaje,
                    "grado": stmt.excluded.grado,
                    "critico": stmt.excluded.critico,
                    "deducciones": stmt.excluded.deducciones,
                    "run_id": stmt.excluded.run_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )

    salud = salud_de_catalogo(notas, por_sku)
    fila = CatalogScore(
        run_id=run.id,
        tenant_id=run.tenant_id,
        store_view_magento_id=run.store_view_magento_id,
        salud=salud.salud,
        grado=salud.grado,
        productos=salud.productos,
        criticos=salud.criticos,
        distribucion=salud.distribucion,
        por_eje={str(k): v for k, v in salud.por_eje.items()},
    )
    session.add(fila)
    session.flush()
    return fila


def tendencia(
    session: Session, tenant_id: int, store_view_magento_id: int, limite: int = 30
) -> list[dict]:
    """Las últimas mediciones, de la más vieja a la más nueva.

    Es la pregunta que el spec llama la única que le interesa a dirección: ¿el
    catálogo mejora o empeora? Una foto no la contesta.
    """
    filas = session.scalars(
        select(CatalogScore)
        .where(
            CatalogScore.tenant_id == tenant_id,
            CatalogScore.store_view_magento_id == store_view_magento_id,
        )
        .order_by(CatalogScore.medido_en.desc())
        .limit(limite)
    ).all()
    return [
        {
            "medido_en": f.medido_en.isoformat() if f.medido_en else None,
            "salud": f.salud,
            "grado": f.grado,
            "criticos": f.criticos,
            "productos": f.productos,
        }
        for f in reversed(filas)
    ]
