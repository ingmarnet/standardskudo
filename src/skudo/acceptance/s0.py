"""Arnés de aceptación de S0.

Cada criterio del spec se comprueba contra el espejo real. La salida es una
lista de resultados, no un booleano: cuando algo falla hay que saber qué.
"""

import argparse
import itertools
import sys

from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from skudo.config import Settings
from skudo.ingest.reconcile import DriftReport, reconcile
from skudo.ingest.source import TenantSource
from skudo.mirror.attributes import distinct_option_ids, option_labels
from skudo.mirror.categories import derive_category_effect
from skudo.mirror.models import (
    Attribute,
    Category,
    CategoryStoreState,
    ProductCategoryAssignment,
    ProductRecord,
    StoreGroup,
    StoreView,
    Tenant,
)
from skudo.mirror.products import (
    SCOPE_PROVENANCE_VOCABULARY,
    SCOPE_STORE,
    SCOPE_WEBSITE,
)
from skudo.mirror.topology import root_category_id as store_root_category_id

# Cuántas particiones divergentes se nombran en el detalle del criterio 1. Un
# conteo solo dice que hay deriva de contenido; una muestra acotada dice por
# dónde empezar a mirar, y 256 nombres en una línea no los lee nadie.
DIVERGING_PARTITION_SAMPLE = 10


# Cuántas asignaciones producto-categoría se recorren para el criterio 5. Un
# conteo entero sobre 200k SKUs es caro y no aporta nada que una muestra
# acotada no confirme ya: mismo criterio de `_procedencia_de_scope`.
CATEGORY_EFFECT_SAMPLE_SIZE = 500


class CriterionResult(BaseModel):
    name: str
    passed: bool
    detail: str


def _espejo_sincronizado(drift: dict[int, DriftReport]) -> CriterionResult:
    """Criterio 1: el espejo coincide con Magento.

    Dos clases de desacuerdo, y las dos reprueban (H1):

    - de CONJUNTO (`needs_full_sync`): falta o sobra un producto.
    - de CONTENIDO (`content_matches`): están los mismos productos y alguno
      tiene otro `updated_at`. Antes de H1 este criterio no lo miraba, así que
      un espejo con un valor rancio pasaba como "sin deriva" — cierto sobre el
      conjunto y engañoso sobre lo que el criterio afirma. Se nombran las
      particiones divergentes, acotadas, porque el remedio es dirigido: son
      ~900 productos de 228.881 por partición en el catálogo piloto.
    """
    drifted = [
        f"store {store_id}: magento={report.magento_count} "
        f"espejo={report.mirror_count} digest_ok={report.digest_matches}"
        for store_id, report in drift.items()
        if report.needs_full_sync
    ]
    drifted += [
        f"store {store_id}: contenido divergente en "
        f"{len(report.diverging_partitions)} de {report.partition_count} "
        "particiones ("
        + ",".join(d.partition for d in report.diverging_partitions[:DIVERGING_PARTITION_SAMPLE])
        + ")"
        for store_id, report in drift.items()
        if not report.content_matches and not report.needs_full_sync
    ]
    return CriterionResult(
        name="espejo_sincronizado",
        passed=not drifted,
        detail="sin deriva" if not drifted else "; ".join(drifted),
    )


def _score_por_store_view(
    session, tenant_id, store_view_ids, drift: dict[int, DriftReport]
) -> CriterionResult:
    """Toda store view declarada tiene registros, y el conteo de cada una cuadra
    con el de SU misma tienda en Magento.

    Lo que NO se exige es que todas tengan el mismo número de productos. Esa
    versión invertía el principio rector del spec: en cuanto un tenant tiene un
    producto solo-PY o solo-BR —un website al que el producto no pertenece, algo
    legítimo y corriente en el tenant piloto, cuyas dos tiendas viven en
    websites distintos—, una ausencia válida se reportaba como defecto.

    La referencia de "población completa" es el conteo de esa tienda en Magento,
    que `reconcile` ya trae, y no el de la tienda de al lado.
    """
    counts = dict(
        session.execute(
            select(ProductRecord.store_view_magento_id, func.count())
            .where(ProductRecord.tenant_id == tenant_id)
            .group_by(ProductRecord.store_view_magento_id)
        ).all()
    )
    empty = [s for s in store_view_ids if not counts.get(s)]
    short = [
        f"store {s}: espejo={counts.get(s, 0)} magento={drift[s].magento_count}"
        for s in store_view_ids
        if s in drift and counts.get(s, 0) != drift[s].magento_count
    ]

    return CriterionResult(
        name="score_por_store_view",
        passed=not empty and not short,
        detail=f"conteos por store view: {counts}"
        + (f"; sin registros: {empty}" if empty else "")
        + ("; conteo distinto al de Magento: " + "; ".join(short) if short else ""),
    )


# Procedencias que afirman una ESCALA concreta en la que el valor se fijó. El
# criterio exige haber visto al menos una: un espejo en el que todo es "global"
# —o todo "desconocido"— no demuestra que la procedencia se resuelva.
_ESCALAS_RESUELTAS = (SCOPE_STORE, SCOPE_WEBSITE)


def _procedencia_de_scope(session, tenant_id) -> CriterionResult:
    """Cada valor efectivo dice EN QUÉ ESCALA se fijó, y esa respuesta se usó.

    La versión anterior de este criterio afirmaba
    `set(r.attributes) == set(r.scope_provenance)`. `resolve_scope` construye
    los dos conjuntos de claves en la misma función a partir de los mismos dos
    dicts: son iguales por construcción, así que la mitad del criterio 3 del
    spec estaba verificada por una tautología. Nunca comprobaba un solo VALOR
    de procedencia, y el producto del arnés de espejo sano tenía
    `store_values: {}`, de modo que jamás había visto una procedencia distinta
    de "global".

    Ahora se comprueban tres cosas, y las tres pueden fallar:

    1. Completitud de claves (lo único que había): sigue siendo necesaria.
    2. Que todo valor de procedencia pertenezca al vocabulario cerrado. Un
       valor fuera de él es un escritor que inventó una escala.
    3. Que se haya observado al menos una escala RESUELTA ("store" o
       "website"). Es la guarda contra la vacuidad, la misma que
       `identidad_de_opciones` aplica con `translated > 0`: un catálogo cuyos
       overrides son todos "desconocido" es exactamente el síntoma de haber
       corrido la ingesta de productos sin la de atributos, y aprobarlo
       certificaría una procedencia que el sistema no sabe calcular.
    """
    rows = session.scalars(
        select(ProductRecord).where(ProductRecord.tenant_id == tenant_id).limit(500)
    ).all()

    sin_procedencia = [
        r.sku for r in rows if set(r.attributes.keys()) != set(r.scope_provenance.keys())
    ]
    fuera_de_vocabulario = sorted(
        {
            valor
            for r in rows
            for valor in r.scope_provenance.values()
            if valor not in SCOPE_PROVENANCE_VOCABULARY
        }
    )
    conteo: dict[str, int] = {}
    for r in rows:
        for valor in r.scope_provenance.values():
            conteo[valor] = conteo.get(valor, 0) + 1
    resueltas = sum(conteo.get(escala, 0) for escala in _ESCALAS_RESUELTAS)

    detalle = f"{len(rows)} registros revisados; procedencias: {conteo}"
    if sin_procedencia:
        detalle += f"; sin procedencia completa: {sin_procedencia[:5]}"
    if fuera_de_vocabulario:
        detalle += f"; procedencias fuera del vocabulario: {fuera_de_vocabulario}"
    if not resueltas:
        detalle += (
            "; ninguna procedencia de escala resuelta (store/website): o el "
            "catálogo no tiene un solo override, o se ingirieron productos sin "
            "haber ingerido antes los atributos y sus scopes declarados"
        )

    return CriterionResult(
        name="procedencia_de_scope",
        passed=not sin_procedencia and not fuera_de_vocabulario and resueltas > 0,
        detail=detalle,
    )


def _identidad_de_opciones(session, tenant_id) -> CriterionResult:
    """Una opción con etiquetas distintas por tienda sigue siendo UNA opción."""
    codes = session.scalars(
        select(Attribute.code).where(
            Attribute.tenant_id == tenant_id, Attribute.frontend_input == "select"
        )
    ).all()

    translated = 0
    for code in codes:
        for option_id in distinct_option_ids(session, tenant_id, code):
            labels = option_labels(session, tenant_id, code, option_id)
            store_labels = {v for k, v in labels.items() if k != 0}
            if len(store_labels) > 1:
                translated += 1

    return CriterionResult(
        name="identidad_de_opciones",
        passed=translated > 0,
        detail=f"{translated} opciones con etiqueta distinta por store view "
        "reconocidas como una sola opción",
    )


def _website_id_of_store(session, tenant_id: int, store_view_id: int) -> int | None:
    """La store view no conoce su website directamente: lo hereda de su grupo,
    igual que `topology.root_category_id`."""
    group_magento_id = session.scalar(
        select(StoreView.group_magento_id).where(
            StoreView.tenant_id == tenant_id, StoreView.magento_id == store_view_id
        )
    )
    if group_magento_id is None:
        return None
    return session.scalar(
        select(StoreGroup.website_magento_id).where(
            StoreGroup.tenant_id == tenant_id, StoreGroup.magento_id == group_magento_id
        )
    )


def _efecto_de_categoria(
    session, tenant_id: int, store_view_ids: list[int]
) -> CriterionResult:
    """Hay datos de categoría en el espejo y `derive_category_effect`,
    alimentado ENTERAMENTE desde filas del espejo, distingue entre store
    views para al menos un producto.

    Sin categorías o sin asignaciones producto-categoría el criterio falla en
    vez de aprobar por vacuidad, igual que `identidad_de_opciones` exige
    `translated > 0`: un espejo vacío no demuestra nada.

    Un efecto `None` (`website_desconocido`, Task A7.3) no es un defecto ni
    una coincidencia: es un control no evaluado. Se cuenta aparte y nunca
    entra en la comparación de "distinto entre store views", así que ni
    infla los aciertos ni se reporta como fallo inventado a partir de un dato
    ausente.

    Lo mismo vale, del otro lado, para el website de la TIENDA:
    `_website_id_of_store` devuelve None cuando la topología de esa store view
    no está espejada, y `derive_category_effect` espera un `int`. Pasarle el
    None hacía que `None not in [1, 2]` fuera verdadero y el veredicto saliera
    `producto_fuera_del_website`: un defecto fabricado a partir de topología
    desconocida, justo en el arnés que existe para vigilar ese error. Hoy ese
    None no llega por accidente —`store_root_category_id` lanza KeyError sobre
    las mismas dos filas unas líneas antes—, pero el arnés no debe depender de
    ese orden para no inventar hallazgos. El par se cuenta como no evaluable.
    """
    if not session.scalar(
        select(func.count()).select_from(Category).where(Category.tenant_id == tenant_id)
    ):
        return CriterionResult(
            name="efecto_de_categoria", passed=False,
            detail="sin categorías en el espejo: nada que evaluar",
        )

    assignments = session.execute(
        select(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
        .where(ProductCategoryAssignment.tenant_id == tenant_id)
        .limit(CATEGORY_EFFECT_SAMPLE_SIZE)
    ).all()
    if not assignments:
        return CriterionResult(
            name="efecto_de_categoria", passed=False,
            detail="sin asignaciones producto-categoría en el espejo: nada que evaluar",
        )

    store_pairs = list(itertools.combinations(store_view_ids, 2))
    discriminating = 0
    evaluated_pairs = 0
    not_evaluated_pairs = 0
    pairs_without_store_website = 0

    for sku, category_magento_id in assignments:
        path = session.scalar(
            select(Category.path).where(
                Category.tenant_id == tenant_id, Category.magento_id == category_magento_id
            )
        )
        if path is None:
            continue  # categoría referenciada pero no espejada: no evaluable

        for store_a, store_b in store_pairs:
            effects = []
            for store_id in (store_a, store_b):
                is_active = session.scalar(
                    select(CategoryStoreState.is_active).where(
                        CategoryStoreState.tenant_id == tenant_id,
                        CategoryStoreState.category_magento_id == category_magento_id,
                        CategoryStoreState.store_view_magento_id == store_id,
                    )
                )
                if is_active is None:
                    break  # sin estado por tienda para esta categoría: no aplica

                try:
                    root = store_root_category_id(session, tenant_id, store_id)
                except KeyError:
                    break  # store view sin topología espejada: no evaluable

                store_website_id = _website_id_of_store(session, tenant_id, store_id)
                if store_website_id is None:
                    # Topología de la tienda no espejada. Es DESCONOCIDO, no
                    # "el producto no está en el website": pasarlo a
                    # derive_category_effect fabricaría el defecto que este
                    # criterio existe para vigilar.
                    pairs_without_store_website += 1
                    break

                website_ids = session.scalar(
                    select(ProductRecord.website_ids).where(
                        ProductRecord.tenant_id == tenant_id,
                        ProductRecord.sku == sku,
                        ProductRecord.store_view_magento_id == store_id,
                    )
                )
                effects.append(
                    derive_category_effect(
                        assignment_path=path,
                        root_category_id=root,
                        is_active_in_store=is_active,
                        product_website_ids=website_ids,
                        store_website_id=store_website_id,
                    )
                )
            else:
                effect_a, effect_b = effects
                if effect_a.is_effective is None or effect_b.is_effective is None:
                    not_evaluated_pairs += 1
                    continue
                evaluated_pairs += 1
                if (effect_a.is_effective, effect_a.reason) != (
                    effect_b.is_effective, effect_b.reason,
                ):
                    discriminating += 1

    return CriterionResult(
        name="efecto_de_categoria",
        passed=discriminating > 0,
        detail=(
            f"{discriminating} producto(s) con efecto de categoría distinto entre "
            f"store views; {evaluated_pairs} par(es) evaluado(s), "
            f"{not_evaluated_pairs} sin evaluar por website_desconocido, "
            f"{pairs_without_store_website} par(es) sin website de la tienda espejado"
        ),
    )


def run_s0_acceptance(
    session: Session, source: TenantSource, store_view_ids: list[int]
) -> list[CriterionResult]:
    tenant_id = source.tenant_id
    # Se reconcilia una sola vez y los dos primeros criterios comparten el
    # resultado: el segundo necesita el conteo de Magento por tienda, que es
    # justo lo que el primero acaba de pedir.
    drift = {
        store_id: reconcile(session, source, store_id) for store_id in store_view_ids
    }
    return [
        _espejo_sincronizado(drift),
        _score_por_store_view(session, tenant_id, store_view_ids, drift),
        _procedencia_de_scope(session, tenant_id),
        _identidad_de_opciones(session, tenant_id),
        _efecto_de_categoria(session, tenant_id, store_view_ids),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica los criterios de S0")
    parser.add_argument("--tenant", required=True, help="código del tenant")
    parser.add_argument("--stores", required=True,
                        help="ids de store view separados por coma, p.ej. 1,3")
    args = parser.parse_args()

    settings = Settings()
    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        tenant = session.scalar(select(Tenant).where(Tenant.code == args.tenant))
        if tenant is None:
            print(f"tenant desconocido: {args.tenant}", file=sys.stderr)
            return 2

        source = TenantSource.from_tenant(tenant, settings.tenant_token(tenant.code))
        try:
            results = run_s0_acceptance(
                session, source, [int(s) for s in args.stores.split(",")]
            )
        finally:
            source.close()

    for result in results:
        print(f"[{'OK ' if result.passed else 'FALLA'}] {result.name}: {result.detail}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
