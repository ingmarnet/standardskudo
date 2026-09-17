"""El mapa de conceptos: qué atributo del espejo implementa cada concepto.

Dos fuentes, en orden de confianza: un seed curado de los universales de Google
(confianza 1.0, es una decisión) y una inferencia conservadora de sinónimos
(confianza < 1.0, es un candidato a confirmar). Un falso sinónimo trata dos
atributos distintos como uno, que es peor que no tenerlo: ante la duda, no se
infiere.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute
from skudo.rules.models import ConceptMap

# Los universales de Google Shopping, mapeados al código del espejo que suele
# implementarlos. Es una decisión, no una medición: por eso confianza 1.0 y
# origen curada. Sólo se siembra el que el tenant realmente tiene.
SEED_GOOGLE: dict[str, str] = {
    "title": "name",
    "description": "description",
    "image_link": "image",
    "price": "price",
    "availability": "status",
    "brand": "brand",
    "gtin": "gtin",
    "mpn": "mpn",
}

# Familias de sinónimos conocidos. Cada tupla es un concepto: su primer elemento
# es el canónico, el resto son formas equivalentes. La lista es corta y curada a
# propósito: es lo que evita que la inferencia por parecido invente pares falsos.
SINONIMOS_CONOCIDOS: tuple[tuple[str, ...], ...] = (
    ("color", "colour"),
    ("talle", "size", "tamano", "tamaño"),
    ("peso", "weight"),
    ("marca", "brand"),
    ("material", "materials"),
    ("genero", "gender", "género"),
)

# Confianza de un sinónimo inferido: alto, pero < 1.0 para que exija confirmación.
CONFIANZA_SINONIMO = 0.8

# Puente de vocabulario: el piso (floor.py) requiere atributos por el NOMBRE
# de Google Shopping (`color`, `size`, `gender`, `age_group`), pero el espejo
# rara vez usa esos nombres tal cual (`talle`, `genero`...) y `age_group` no
# tiene ninguna familia de sinónimos declarada. Sin este puente, esos
# requisitos nacen sin_mapeo aunque el tenant sí tenga el atributo, y el piso
# de indumentaria queda inerte. Cada entrada es una lista de candidatos de
# atributo del espejo, en el orden de preferencia con que se elige el
# primero que el tenant realmente tenga (inglés primero, luego español).
SEED_CATEGORIA: dict[str, tuple[str, ...]] = {
    "color": ("color", "colour"),
    "size": ("size", "talle", "tamano", "tamaño"),
    "gender": ("gender", "genero", "género", "sexo"),
    "age_group": ("age_group", "edad", "grupo_edad", "rango_edad"),
}


def _existe(session, tenant_id, canonical, attribute_code) -> bool:
    return session.scalar(
        select(ConceptMap.id).where(
            ConceptMap.tenant_id == tenant_id,
            ConceptMap.canonical == canonical,
            ConceptMap.attribute_code == attribute_code,
        )
    ) is not None


def _codigos(session, tenant_id) -> set[str]:
    return set(session.scalars(
        select(Attribute.code).where(Attribute.tenant_id == tenant_id)
    ).all())


def sembrar(session: Session, tenant_id: int) -> list[ConceptMap]:
    """Siembra los universales de Google que el tenant realmente tiene, más el
    puente de vocabulario de categoría (SEED_CATEGORIA) hacia el primer
    atributo candidato que el tenant tenga. Idempotente.

    El puente de categoría sólo siembra cuando el tenant tiene al menos uno de
    los candidatos: si no tiene ninguno, ese nombre de Google queda sin
    sembrar y el piso lo verá honestamente sin_mapeo, no inventado.
    """
    codigos = _codigos(session, tenant_id)
    creados = []
    for canonical, code in sorted(SEED_GOOGLE.items()):
        if code not in codigos or _existe(session, tenant_id, canonical, code):
            continue
        fila = ConceptMap(tenant_id=tenant_id, canonical=canonical,
                          attribute_code=code, relation="equivalente",
                          confidence=1.0, origin="curada")
        session.add(fila)
        creados.append(fila)
    for google_name, candidatos in SEED_CATEGORIA.items():
        code = next((c for c in candidatos if c in codigos), None)
        if code is None or _existe(session, tenant_id, google_name, code):
            continue
        fila = ConceptMap(tenant_id=tenant_id, canonical=google_name,
                          attribute_code=code, relation="equivalente",
                          confidence=1.0, origin="curada")
        session.add(fila)
        creados.append(fila)
    session.flush()
    return creados


def inferir_sinonimos(session: Session, tenant_id: int) -> list[ConceptMap]:
    """Infiere sinónimos SÓLO entre las familias conocidas. Conservador a propósito.

    No compara por prefijo ni por distancia de edición: `precio` y
    `precio_especial` comparten prefijo y son conceptos distintos. Sólo se
    infiere lo que una familia curada declara sinónimo.
    """
    codigos = _codigos(session, tenant_id)
    creados = []
    for familia in SINONIMOS_CONOCIDOS:
        canonical = familia[0]
        presentes = [c for c in familia if c in codigos]
        if len(presentes) < 2:
            continue  # hace falta al menos el canónico y una variante
        for code in sorted(presentes):
            relacion = "equivalente" if code == canonical else "sinonimo"
            conf = 1.0 if code == canonical else CONFIANZA_SINONIMO
            origen = "curada" if code == canonical else "inferida"
            if _existe(session, tenant_id, canonical, code):
                continue
            fila = ConceptMap(tenant_id=tenant_id, canonical=canonical,
                              attribute_code=code, relation=relacion,
                              confidence=conf, origin=origen)
            session.add(fila)
            creados.append(fila)
    session.flush()
    return creados
