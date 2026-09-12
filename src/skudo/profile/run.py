"""La pasada del perfilador sobre una store view.

Recorre el espejo UN ATTRIBUTE SET A LA VEZ. No es un detalle de estilo: el
catálogo piloto tiene 228.881 productos por store view y cargarlos todos en
memoria para agruparlos después convierte una pasada de minutos en una que no
termina en una máquina normal. Cargar un set —el mayor son decenas de miles de
filas— acota la memoria por el set más grande y no por el catálogo.
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute, Category, ProductCategoryAssignment, ProductRecord
from skudo.profile.coverage import ProductoPerfilado, coverage_vector
from skudo.profile.distribution import value_stats
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun, ValueStats
from skudo.profile.partition import (
    MAX_CARD,
    MIN_GANANCIA,
    MIN_PARTICION,
    SIN_VALOR,
    ambiguity,
    choose_splitter,
)
from skudo.profile.states import codes_by_set, sets_by_code


def _depth_by_category(session: Session, tenant_id: int) -> dict[int, int]:
    """Profundidad de cada categoría, para elegir la más específica de un producto."""
    filas = session.execute(
        select(Category.magento_id, Category.path).where(Category.tenant_id == tenant_id)
    ).all()
    return {magento_id: len(path or []) for magento_id, path in filas}


def _frontend_inputs(session: Session, tenant_id: int) -> dict[str, str]:
    filas = session.execute(
        select(Attribute.code, Attribute.frontend_input).where(
            Attribute.tenant_id == tenant_id
        )
    ).all()
    return dict(filas)


def _categorias_por_sku(session: Session, tenant_id: int) -> dict[str, tuple[int, ...]]:
    filas = session.execute(
        select(
            ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id
        ).where(ProductCategoryAssignment.tenant_id == tenant_id)
    ).all()
    acumulado: dict[str, list[int]] = {}
    for sku, categoria in filas:
        acumulado.setdefault(sku, []).append(categoria)
    return {sku: tuple(sorted(cats)) for sku, cats in acumulado.items()}


def _productos_del_set(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    attribute_set_id: int | None,
    categorias: dict[str, tuple[int, ...]],
) -> list[ProductoPerfilado]:
    condicion = (
        ProductRecord.attribute_set_id.is_(None)
        if attribute_set_id is None
        else ProductRecord.attribute_set_id == attribute_set_id
    )
    filas = session.execute(
        select(ProductRecord.sku, ProductRecord.attributes, ProductRecord.attribute_set_id)
        .where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
            condicion,
        )
        .order_by(ProductRecord.sku)  # el determinismo empieza aquí
    ).all()
    return [
        ProductoPerfilado(
            sku=sku,
            attributes=attributes or {},
            attribute_set_id=set_id,
            categorias=categorias.get(sku, ()),
        )
        for sku, attributes, set_id in filas
    ]


def profile_store_view(
    session: Session, tenant_id: int, store_view_magento_id: int
) -> ProfileRun:
    """Perfila una store view entera y devuelve la pasada ya sellada."""
    generacion = session.scalar(
        select(func.coalesce(func.max(ProductRecord.sync_generation), 0)).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
    total = session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
    run = ProfileRun(
        tenant_id=tenant_id,
        store_view_magento_id=store_view_magento_id,
        mirror_sync_generation=generacion or 0,
        thresholds={
            "MIN_PARTICION": MIN_PARTICION,
            "MAX_CARD": MAX_CARD,
            "MIN_GANANCIA": MIN_GANANCIA,
        },
        product_count=total or 0,
    )
    session.add(run)
    session.flush()

    por_codigo = sets_by_code(session, tenant_id)
    por_set = codes_by_set(session, tenant_id)
    entradas = _frontend_inputs(session, tenant_id)
    profundidad = _depth_by_category(session, tenant_id)
    categorias = _categorias_por_sku(session, tenant_id)

    sets = [
        s
        for (s,) in session.execute(
            select(ProductRecord.attribute_set_id)
            .where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
            )
            .distinct()
            .order_by(ProductRecord.attribute_set_id)
        ).all()
    ]

    huella: list = []
    for attribute_set_id in sets:
        productos = _productos_del_set(
            session, tenant_id, store_view_magento_id, attribute_set_id, categorias
        )
        if attribute_set_id is None:
            # Sin set no se puede distinguir vacío de no_aplica: se cuenta y no
            # se mide. Inventar una cobertura aquí sería inventar un hecho.
            _escribir_particion(
                session, run, None, "set_desconocido", None, None,
                len(productos), None, "set desconocido", {}, {}, huella,
            )
            continue

        codes = por_set.get(attribute_set_id, ())
        eleccion = choose_splitter(productos, codes, por_codigo, entradas, profundidad)
        grupos = eleccion.groups or {SIN_VALOR: productos}
        for valor, grupo in sorted(grupos.items()):
            vector = coverage_vector(grupo, codes, por_codigo)
            stats = {
                code: value_stats(
                    [
                        g.attributes[code]
                        for g in grupo
                        if str(g.attributes.get(code, "")).strip() != ""
                    ],
                    entradas.get(code, "text"),
                )
                for code in codes
            }
            _escribir_particion(
                session,
                run,
                attribute_set_id,
                "ninguno" if eleccion.splitter is None else eleccion.splitter.kind,
                None if eleccion.splitter is None else eleccion.splitter.key,
                None if eleccion.splitter is None else valor,
                len(grupo),
                None,
                eleccion.reason,
                vector,
                stats,
                huella,
            )

    run.digest = hashlib.sha256(
        json.dumps(huella, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    run.finished_at = datetime.now(UTC)
    session.flush()
    return run


def _escribir_particion(
    session, run, attribute_set_id, kind, key, valor, n, ambiguedad, razon, vector, stats, huella
) -> None:
    """Escribe una partición con su cobertura y sus distribuciones.

    Va alimentando `huella` con lo mismo que escribe: el sello de la pasada se
    calcula sobre lo que de verdad quedó en la base, no sobre una segunda
    versión de los datos que podría divergir sin que nadie se entere.
    """
    calculada = ambiguity(vector) if vector else ambiguedad
    particion = ProfilePartition(
        run_id=run.id,
        attribute_set_id=attribute_set_id,
        splitter_kind=kind,
        splitter_key=key,
        splitter_value=valor,
        product_count=n,
        ambiguity=calculada,
        decision_reason=razon,
    )
    session.add(particion)
    session.flush()
    huella.append([attribute_set_id, kind, key, valor, n, calculada, razon])

    for code in sorted(vector):
        cuenta = vector[code]
        session.add(
            AttributeCoverage(
                partition_id=particion.id,
                attribute_code=code,
                presente=cuenta.presente,
                vacio=cuenta.vacio,
                no_aplica=cuenta.no_aplica,
                desconocido=cuenta.desconocido,
                coverage=cuenta.cobertura,
            )
        )
        huella.append([code, cuenta.presente, cuenta.vacio, cuenta.desconocido, cuenta.cobertura])

    for code in sorted(stats):
        s = stats[code]
        session.add(
            ValueStats(
                partition_id=particion.id,
                attribute_code=code,
                kind=s.kind,
                n_present=s.n_present,
                n_ambiguous=s.n_ambiguous,
                minimum=s.minimum,
                p05=s.p05,
                p50=s.p50,
                p95=s.p95,
                maximum=s.maximum,
                distinct_values=s.distinct_values,
                mode_share=s.mode_share,
                discriminating_power=s.discriminating_power,
                top_values=s.top_values,
            )
        )
        huella.append([code, s.kind, s.n_present, s.n_ambiguous, s.p50, s.discriminating_power])
    session.flush()
