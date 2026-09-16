"""La pasada de detección sobre una store view."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.findings.catalog import Ficha, evaluar
from skudo.findings.models import DetectorCoverage, Finding, FindingRun
from skudo.mirror.models import ProductCategoryAssignment, ProductRecord


def fichas_de(session: Session, tenant_id: int, store_view_magento_id: int) -> list[Ficha]:
    categorias: dict[str, list[int]] = {}
    for sku, cat in session.execute(
        select(
            ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id
        ).where(ProductCategoryAssignment.tenant_id == tenant_id)
    ).all():
        categorias.setdefault(sku, []).append(cat)

    filas = session.execute(
        select(
            ProductRecord.sku,
            ProductRecord.attributes,
            ProductRecord.attribute_set_id,
            ProductRecord.type_id,
        )
        .where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
        .order_by(ProductRecord.sku)  # el orden estable empieza acá
    ).all()
    return [
        Ficha(
            sku=sku,
            attributes=attrs or {},
            attribute_set_id=set_id,
            type_id=type_id,
            categorias=tuple(sorted(categorias.get(sku, ()))),
        )
        for sku, attrs, set_id, type_id in filas
    ]


def detect_store_view(
    session: Session, tenant_id: int, store_view_magento_id: int
) -> FindingRun:
    """Corre todos los detectores y deja la pasada escrita."""
    generacion = session.scalar(
        select(func.coalesce(func.max(ProductRecord.sync_generation), 0)).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
    fichas = fichas_de(session, tenant_id, store_view_magento_id)
    run = FindingRun(
        tenant_id=tenant_id,
        store_view_magento_id=store_view_magento_id,
        mirror_sync_generation=generacion or 0,
        product_count=len(fichas),
    )
    session.add(run)
    session.flush()

    for resultado in evaluar(fichas):
        c = resultado.cobertura
        session.add(
            DetectorCoverage(
                run_id=run.id,
                detector=c.detector,
                evaluados=c.evaluados,
                no_aplica=c.no_aplica,
                no_evaluado=c.no_evaluado,
                motivo_no_aplica=c.motivo_no_aplica[:512],
            )
        )
        for h in resultado.hallazgos:
            session.add(
                Finding(
                    run_id=run.id,
                    code=h.code,
                    axis=h.axis,
                    severity=h.severity,
                    subject_type=h.subject_type,
                    subject_key=h.subject_key[:255],
                    evidence=h.evidence,
                )
            )
    run.finished_at = datetime.now(UTC)
    session.flush()
    return run


def findings_report(session: Session, run: FindingRun) -> dict:
    """El resumen de una pasada: hallazgos por detector, CON su cobertura al lado.

    Los dos números viajan juntos siempre. Separarlos es cómo un «197» se
    convierte en un porcentaje sobre el denominador equivocado, que es
    exactamente el error que estos detectores existen para no repetir.
    """
    coberturas = {
        c.detector: c
        for c in session.scalars(
            select(DetectorCoverage).where(DetectorCoverage.run_id == run.id)
        )
    }
    por_codigo = session.execute(
        select(Finding.code, Finding.severity, Finding.axis, func.count())
        .where(Finding.run_id == run.id)
        .group_by(Finding.code, Finding.severity, Finding.axis)
        .order_by(func.count().desc())
    ).all()

    # `nombres_repetidos` emite dos códigos distintos desde un solo detector.
    de_detector = {
        "variantes_sueltas": "nombres_repetidos",
        "nombre_repetido": "nombres_repetidos",
    }

    hallazgos = []
    for code, severity, axis, n in por_codigo:
        c = coberturas.get(de_detector.get(code, code))
        hallazgos.append(
            {
                "code": code,
                "eje": axis,
                "severidad": severity,
                "hallazgos": n,
                "evaluados": c.evaluados if c else None,
                "porcentaje": round(100 * n / c.evaluados, 1) if c and c.evaluados else None,
                "no_aplica": c.no_aplica if c else None,
                "no_evaluado": c.no_evaluado if c else None,
            }
        )

    return {
        "run_id": run.id,
        "store_view": run.store_view_magento_id,
        "generacion_espejo": run.mirror_sync_generation,
        "productos": run.product_count,
        "hallazgos_totales": sum(h["hallazgos"] for h in hallazgos),
        "por_deteccion": hallazgos,
        "exclusiones": {
            c.detector: c.motivo_no_aplica for c in coberturas.values() if c.motivo_no_aplica
        },
    }
