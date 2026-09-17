"""google_floor + espejo → reglas de piso externo.

Lee el google_category_id_int de cada producto (lo puso Standard_GoogleCategory),
busca los requisitos que aplican, y emite reglas origin=piso_externo. El piso no
se infiere y no se rechaza: nace aceptado. Un requisito cuyo atributo el mapa de
conceptos no conoce nace en aviso, no puntúa contra algo que no sabe leer.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductCategoryAssignment, ProductRecord
from skudo.rules.floor_seed import SEED_FLOOR
from skudo.rules.models import ConceptMap, GoogleFloor, Rule

GOOGLE_CATEGORY_ATTR = "google_category_id_int"


def cargar_seed(session: Session) -> int:
    """Puebla google_floor desde SEED_FLOOR. Idempotente: no re-inserta lo que ya está."""
    ya = session.scalar(select(GoogleFloor.id).limit(1))
    if ya is not None:
        return 0
    for fila in SEED_FLOOR:
        session.add(GoogleFloor(
            category_group=fila["category_group"],
            google_category_min=fila["min"], google_category_max=fila["max"],
            google_attribute=fila["google_attribute"], requirement=fila["requirement"],
            axis=fila["axis"], applicability=fila["applicability"], note=fila["note"],
        ))
    session.flush()
    return len(SEED_FLOOR)


def _mapa_google_a_espejo(session, tenant_id) -> dict[str, str]:
    """Para cada atributo de Google (canónico), el código del espejo que lo implementa."""
    filas = session.execute(
        select(ConceptMap.canonical, ConceptMap.attribute_code)
        .where(ConceptMap.tenant_id == tenant_id)
    ).all()
    # Preferimos el equivalente de mayor confianza; aquí basta el primero estable.
    mapa: dict[str, str] = {}
    for canonical, code in sorted(filas):
        mapa.setdefault(canonical, code)
    return mapa


def _requisitos_para(session, google_category_id: int) -> list[GoogleFloor]:
    filas = session.scalars(select(GoogleFloor)).all()
    aplica = []
    for f in filas:
        if (
            f.google_category_min is None  # universal
            or f.google_category_min <= google_category_id <= f.google_category_max
        ):
            aplica.append(f)
    return aplica


def generar_piso(session: Session, tenant_id: int) -> list[Rule]:
    """Genera las reglas de piso externo del tenant desde el espejo y el seed."""
    mapa = _mapa_google_a_espejo(session, tenant_id)

    # google_category_id_int por SKU (de los atributos del espejo).
    cat_por_producto = {}
    for sku, attrs in session.execute(
        select(ProductRecord.sku, ProductRecord.attributes)
        .where(ProductRecord.tenant_id == tenant_id)
    ).all():
        valor = (attrs or {}).get(GOOGLE_CATEGORY_ATTR)
        if valor is None:
            continue
        try:
            cat_por_producto[sku] = int(valor)
        except (TypeError, ValueError):
            continue

    # categoría de Magento por SKU (la primera estable, para el scope).
    categoria_magento = {}
    for sku, cat in session.execute(
        select(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
        .where(ProductCategoryAssignment.tenant_id == tenant_id)
        .order_by(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
    ).all():
        categoria_magento.setdefault(sku, cat)

    # Deduplicamos por (categoria_magento, google_attribute): una regla por
    # categoría y requisito, no una por producto.
    vistos: set[tuple[int, str]] = set()
    reglas: list[Rule] = []
    for sku in sorted(cat_por_producto):
        google_id = cat_por_producto[sku]
        scope_cat = categoria_magento.get(sku)
        if scope_cat is None:
            continue
        for req in _requisitos_para(session, google_id):
            clave = (scope_cat, req.google_attribute)
            if clave in vistos:
                continue
            vistos.add(clave)
            espejo = mapa.get(req.google_attribute)
            definition = {
                "google_attribute": req.google_attribute,
                "requirement": req.requirement,
                "applicability": req.applicability,
            }
            if espejo is None:
                definition["sin_mapeo"] = True
                status = "aviso"
            else:
                definition["espejo_attribute"] = espejo
                status = "aceptada" if req.requirement == "required" else "aviso"
            reglas.append(Rule(
                tenant_id=tenant_id, scope_kind="category", scope_key=str(scope_cat),
                store_view_magento_id=None, axis=req.axis, kind="obligatoriedad",
                definition=definition, confidence=1.0, evidence_count=0,
                exceptions=[], status=status, origin="piso_externo",
            ))
    for r in reglas:
        session.add(r)
    session.flush()
    return reglas
