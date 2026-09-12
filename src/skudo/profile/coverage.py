"""El vector de cobertura de un grupo de productos.

La cobertura de un atributo es `presente / (presente + vacio)`. Lo que NO entra
en el denominador es tan importante como lo que entra: un producto cuyo set se
desconoce no dice nada sobre si ese atributo debería estar, y meterlo en el
denominador convierte ignorancia en carencia.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from skudo.profile.states import State, attribute_state


@dataclass(frozen=True)
class ProductoPerfilado:
    """Lo mínimo que el perfilador necesita de un producto del espejo."""

    sku: str
    attributes: dict
    attribute_set_id: int | None
    categorias: tuple[int, ...]


@dataclass(frozen=True)
class Counts:
    presente: int = 0
    vacio: int = 0
    no_aplica: int = 0
    desconocido: int = 0

    @property
    def evaluables(self) -> int:
        return self.presente + self.vacio

    @property
    def cobertura(self) -> float | None:
        """`None` cuando no hay denominador, jamás `0.0`.

        Devolver cero aquí sería afirmar "ningún producto lo tiene" cuando lo
        cierto es "no se pudo mirar". Toda la aritmética de S1a propaga ese
        `None` en vez de sustituirlo.
        """
        if self.evaluables == 0:
            return None
        return self.presente / self.evaluables


def coverage_vector(
    products: Iterable[ProductoPerfilado],
    codes: Sequence[str],
    sets_by_code: dict[str, frozenset[int]],
) -> dict[str, Counts]:
    """Cuenta los cuatro estados de cada atributo sobre un grupo de productos."""
    conteos: dict[str, Counter] = {code: Counter() for code in sorted(codes)}
    for product in products:
        for code, cuenta in conteos.items():
            estado = attribute_state(
                product.attributes, product.attribute_set_id, code, sets_by_code
            )
            cuenta[estado] += 1
    return {
        code: Counts(
            presente=c[State.PRESENTE],
            vacio=c[State.VACIO],
            no_aplica=c[State.NO_APLICA],
            desconocido=c[State.DESCONOCIDO],
        )
        for code, c in conteos.items()
    }
