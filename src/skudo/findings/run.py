"""La pasada de detección sobre una store view."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.findings.catalog import CANDIDATO, MEDIA, Cobertura, Ficha, Resultado, evaluar
from skudo.findings.models import DetectorCoverage, Finding, FindingRun
from skudo.findings.filter_blind import evaluar_filtro_ciego
from skudo.findings.rules_eval import ReglaEvaluable, evaluar_regla
from skudo.findings.traduccion import evaluar_nombre_sin_traducir
from skudo.mirror.models import (
    Attribute,
    ProductCategoryAssignment,
    ProductRecord,
    ProductSignal,
)
from skudo.mirror.store_settings import muestra_sin_stock
from skudo.profile.states import sets_by_code
from skudo.rules.models import Rule, RulesetSnapshot


def fichas_de(session: Session, tenant_id: int, store_view_magento_id: int) -> list[Ficha]:
    categorias: dict[str, list[int]] = {}
    for sku, cat in session.execute(
        select(
            ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id
        ).where(ProductCategoryAssignment.tenant_id == tenant_id)
        .execution_options(yield_per=5000)
    ).all():
        categorias.setdefault(sku, []).append(cat)

    stock_por_sku = dict(
        session.execute(
            select(ProductSignal.sku, ProductSignal.is_in_stock).where(
                ProductSignal.tenant_id == tenant_id,
                ProductSignal.store_view_magento_id == store_view_magento_id,
            )
            .execution_options(yield_per=5000)
        ).all()
    )
    muestra = muestra_sin_stock(session, tenant_id, store_view_magento_id)

    filas = session.execute(
        select(
            ProductRecord.sku,
            ProductRecord.attributes,
            ProductRecord.attribute_set_id,
            ProductRecord.type_id,
            ProductRecord.parent_skus,
            ProductRecord.variation_attributes,
        )
        .where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
        .order_by(ProductRecord.sku)  # el orden estable empieza acá
        .execution_options(yield_per=5000)
    ).all()
    return [
        Ficha(
            sku=sku,
            attributes=attrs or {},
            attribute_set_id=set_id,
            type_id=type_id,
            categorias=tuple(sorted(categorias.get(sku, ()))),
            is_in_stock=stock_por_sku.get(sku),
            muestra_sin_stock=muestra,
            parent_skus=tuple(parents or ()),
            variation_attributes=tuple(ejes or ()),
        )
        for sku, attrs, set_id, type_id, parents, ejes in filas
    ]


def _escribir_resultado(
    session: Session,
    run: FindingRun,
    resultado: Resultado,
    rule_id: int | None,
    version: int | None,
) -> None:
    """Escribe la cobertura y los hallazgos de UN resultado (detector o regla).

    `rule_id`/`version` son None para un detector especial; para una regla,
    su id y la versión del snapshot con que corrió la pasada. Es el único
    lugar que escribe `DetectorCoverage`/`Finding`: detectores y reglas pasan
    por acá para que la atribución no se escriba dos veces distinto.
    """
    _escribir_cobertura(session, run, resultado.cobertura)
    _escribir_hallazgos(session, run, resultado.hallazgos, rule_id, version)


def _escribir_cobertura(session: Session, run: FindingRun, c: Cobertura) -> None:
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


def _escribir_hallazgos(session, run, hallazgos, rule_id, version) -> None:
    for h in hallazgos:
        session.add(
            Finding(
                run_id=run.id,
                code=h.code,
                axis=h.axis,
                severity=h.severity,
                subject_type=h.subject_type,
                subject_key=h.subject_key[:255],
                evidence=h.evidence,
                rule_id=rule_id,
                ruleset_version=version,
            )
        )


def _fusionar_cobertura(cubs: list[Cobertura], total: int) -> Cobertura:
    """Fusiona en UNA las coberturas de varias reglas que comparten código.

    Dos reglas del mismo kind+attribute con distinto scope (ej.
    `obligatoriedad:color` en el set 4 y en el 9) producen el mismo código y
    chocarían contra el único (run_id, detector). Como los scopes son
    disjuntos, `evaluados` es la SUMA (ningún producto lo evalúan dos reglas).
    El resto del universo se reparte: `no_evaluado` es lo que ninguna pudo
    ubicar (acotado a lo que queda) y `no_aplica` es el resto, de modo que los
    tres cierran en `total`. Esto es exacto cuando todo producto tiene set
    (el caso real); si no, el acote evita una cuenta negativa.
    """
    if len(cubs) == 1:
        return cubs[0]
    evaluados = sum(c.evaluados for c in cubs)
    no_evaluado = min(sum(c.no_evaluado for c in cubs), max(total - evaluados, 0))
    no_aplica = max(total - evaluados - no_evaluado, 0)
    return Cobertura(cubs[0].detector, evaluados, no_aplica, no_evaluado,
                     cubs[0].motivo_no_aplica)


def _severidad_base(r: Rule) -> str:
    """La severidad con que puntúa una regla ACEPTADA, según su kind."""
    if r.kind == "obligatoriedad":
        return r.definition.get("severidad", MEDIA)
    return CANDIDATO  # rango, formato


def _reglas_del_snapshot(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    ruleset_version: int | None = None,
) -> tuple[list[ReglaEvaluable], int | None]:
    """Las reglas activas del snapshot pedido (o el último) como ReglaEvaluable.

    Sin snapshot, no hay reglas: el motor no aporta y la pasada lo declara con
    ruleset_version = None. Los detectores especiales corren igual.
    """
    q = select(RulesetSnapshot).where(
        RulesetSnapshot.tenant_id == tenant_id,
        RulesetSnapshot.store_view_magento_id == store_view_magento_id,
    )
    if ruleset_version is not None:
        q = q.where(RulesetSnapshot.version == ruleset_version)
    snap = session.scalars(q.order_by(RulesetSnapshot.version.desc()).limit(1)).first()
    if snap is None:
        return [], None
    filas = session.scalars(
        select(Rule).where(Rule.id.in_(snap.rule_ids)).order_by(Rule.id)
    ).all()
    reglas = [
        ReglaEvaluable(
            id=r.id, kind=r.kind, axis=r.axis,
            # una regla en `aviso` no penaliza: su severidad efectiva es 'aviso'
            severity=("aviso" if r.status == "aviso" else _severidad_base(r)),
            scope_kind=r.scope_kind, scope_key=r.scope_key,
            store_view=r.store_view_magento_id, definition=r.definition,
        )
        for r in filas
        if r.kind in ("obligatoriedad", "rango", "formato")
        and "attribute" in (r.definition or {})
        # el snapshot congela IDs, no estado: una regla que ERA aceptada al
        # snapshotear y después pasó a rechazada/borrador no debe penalizar
        # sólo porque un `evaluate --ruleset <version vieja>` la trae de vuelta.
        # El estado que manda es el VIVO, no el de cuando se armó el snapshot.
        and r.status in ("aceptada", "aviso")
    ]
    return reglas, snap.version


def _skus_por_categoria(session: Session, tenant_id: int) -> dict[int, set[str]]:
    """SKUs asignados a cada categoría, para acotar las reglas con scope `category`."""
    por_categoria: dict[int, set[str]] = {}
    for sku, cat in session.execute(
        select(
            ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id
        ).where(ProductCategoryAssignment.tenant_id == tenant_id)
        .execution_options(yield_per=5000)
    ).all():
        por_categoria.setdefault(cat, set()).add(sku)
    return por_categoria


def detect_store_view(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    ruleset_version: int | None = None,
) -> FindingRun:
    """Corre todos los detectores y el motor de reglas, y deja la pasada escrita."""
    generacion = session.scalar(
        select(func.coalesce(func.max(ProductRecord.sync_generation), 0)).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
    fichas = fichas_de(session, tenant_id, store_view_magento_id)
    reglas, version = _reglas_del_snapshot(
        session, tenant_id, store_view_magento_id, ruleset_version
    )
    run = FindingRun(
        tenant_id=tenant_id,
        store_view_magento_id=store_view_magento_id,
        mirror_sync_generation=generacion or 0,
        product_count=len(fichas),
        ruleset_version=version,
    )
    session.add(run)
    session.flush()

    # 1) detectores especiales
    tenant_code = session.execute(
        __import__("sqlalchemy").text("SELECT code FROM tenant WHERE id = :tid"), 
        {"tid": tenant_id}
    ).scalar()
    for resultado in evaluar(fichas, tenant_id=tenant_code):
        _escribir_resultado(session, run, resultado, rule_id=None, version=None)

    # 1b) nombre sin traducir: pares de store view. No es un detector de fichas:
    # necesita las store views hermanas y sus locales, así que corre acá.
    for resultado in evaluar_nombre_sin_traducir(
        session, tenant_id, store_view_magento_id, fichas
    ):
        _escribir_resultado(session, run, resultado, rule_id=None, version=None)

    # 2) motor de reglas, sobre el snapshot resuelto arriba
    por_categoria = (
        _skus_por_categoria(session, tenant_id)
        if any(r.scope_kind == "category" for r in reglas)
        else {}
    )
    sets = sets_by_code(session, tenant_id)
    # Los hallazgos se escriben por regla (atribución exacta por rule_id); la
    # cobertura se acumula por código y se escribe fusionada al final, porque
    # dos reglas del mismo kind+attribute con distinto scope comparten código.
    cobertura_por_codigo: dict[str, list[Cobertura]] = {}
    for regla in reglas:
        fichas_regla = fichas
        if regla.scope_kind == "category":
            skus = por_categoria.get(int(regla.scope_key), set())
            fichas_regla = [f for f in fichas if f.sku in skus]
        resultado = evaluar_regla(regla, fichas_regla, sets)
        _escribir_hallazgos(session, run, resultado.hallazgos, regla.id, version)
        cobertura_por_codigo.setdefault(
            resultado.cobertura.detector, []
        ).append(resultado.cobertura)

    for cubs in cobertura_por_codigo.values():
        _escribir_cobertura(session, run, _fusionar_cobertura(cubs, len(fichas)))

    # 3) filter-blind: todo atributo FILTRABLE vacío donde aplica. Universal
    # (no depende del snapshot), reusa `sets` y la misma máquina de estados.
    filtrables = session.scalars(
        select(Attribute.code).where(
            Attribute.tenant_id == tenant_id,
            Attribute.is_filterable.is_(True),
        )
    ).all()
    for resultado in evaluar_filtro_ciego(fichas, filtrables, sets):
        _escribir_hallazgos(session, run, resultado.hallazgos, None, None)
        _escribir_cobertura(session, run, resultado.cobertura)

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

    # Un código `regla:*` (kind + attribute) puede venir de MÁS DE UNA regla
    # si dos reglas comparten kind+attribute con distinto scope (ej.
    # `obligatoriedad:color` en el attribute_set 4 y en el 9: ambas producen
    # el mismo código, el conteo agrupado de arriba las suma). `rule_id` sólo
    # puede atribuirse a una regla sin ambigüedad, así que se arma el
    # conjunto de rule_ids distintos por código y, si hay más de uno, la fila
    # queda con `rule_id = None` en vez de mentir con cualquiera de los dos.
    reglas_por_codigo: dict[str, set[int]] = {}
    for code, rule_id in session.execute(
        select(Finding.code, Finding.rule_id)
        .where(Finding.run_id == run.id, Finding.rule_id.is_not(None))
        .distinct()
    ).all():
        reglas_por_codigo.setdefault(code, set()).add(rule_id)

    hallazgos = []
    for code, severity, axis, n in por_codigo:
        c = coberturas.get(de_detector.get(code, code))
        origen = "regla" if code.startswith("regla:") else "detector"
        fila = {
            "code": code,
            "eje": axis,
            "severidad": severity,
            "hallazgos": n,
            "evaluados": c.evaluados if c else None,
            "porcentaje": round(100 * n / c.evaluados, 1) if c and c.evaluados else None,
            "no_aplica": c.no_aplica if c else None,
            "no_evaluado": c.no_evaluado if c else None,
            "origen": origen,
        }
        if origen == "regla":
            distintos = reglas_por_codigo.get(code, set())
            fila["rule_id"] = next(iter(distintos)) if len(distintos) == 1 else None
        hallazgos.append(fila)

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
