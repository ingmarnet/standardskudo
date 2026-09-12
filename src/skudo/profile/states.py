"""Los cuatro estados de un dato, derivados del espejo.

El spec los declara innegociables: `presente`, `vacio`, `no_aplica`,
`desconocido`. Colapsar dos de ellos es el fallo que el principio rector del
proyecto nombra como el peor posible, y las dos formas de colapsarlos son
concretas y están probadas aquí: tratar `"0"` como ausencia, y tratar una clave
ausente como "no aplica".
"""

from collections import defaultdict
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute


class State(str, Enum):
    PRESENTE = "presente"
    VACIO = "vacio"
    NO_APLICA = "no_aplica"
    DESCONOCIDO = "desconocido"


def attribute_state(
    attributes: dict,
    attribute_set_id: int | None,
    code: str,
    sets_by_code: dict[str, frozenset[int]],
) -> State:
    """El estado de UN atributo en UN producto.

    El orden de las comprobaciones no es casual: la ignorancia gana a todo lo
    demás. Si no sabemos a qué set pertenece el producto, o no conocemos el
    atributo, ningún valor observado autoriza a concluir nada.
    """
    declared = sets_by_code.get(code)
    if declared is None or attribute_set_id is None:
        return State.DESCONOCIDO
    if attribute_set_id not in declared:
        return State.NO_APLICA
    if code not in attributes:
        return State.VACIO
    value = attributes[code]
    if value is None:
        return State.VACIO
    if isinstance(value, str) and value.strip() == "":
        return State.VACIO
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return State.VACIO
    return State.PRESENTE


def sets_by_code(session: Session, tenant_id: int) -> dict[str, frozenset[int]]:
    """A qué attribute sets pertenece cada atributo, según el espejo."""
    filas = session.execute(
        select(Attribute.code, Attribute.attribute_set_ids).where(
            Attribute.tenant_id == tenant_id
        )
    ).all()
    return {code: frozenset(ids or []) for code, ids in filas}


def codes_by_set(session: Session, tenant_id: int) -> dict[int, tuple[str, ...]]:
    """El índice invertido, ordenado.

    El perfilador recorre los ~109 atributos del set de cada producto, no los
    1.066 del catálogo: la diferencia entre 25 y 244 millones de evaluaciones.
    Las tuplas van ordenadas porque el determinismo del perfil depende de que
    el recorrido no dependa del orden de inserción.
    """
    acumulado: dict[int, list[str]] = defaultdict(list)
    for code, ids in sorted(sets_by_code(session, tenant_id).items()):
        for set_id in sorted(ids):
            acumulado[set_id].append(code)
    return {set_id: tuple(codes) for set_id, codes in sorted(acumulado.items())}
