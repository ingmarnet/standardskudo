"""La única autoridad sobre qué transición de una regla es válida.

Ni el CLI ni la capa de curación deciden por su cuenta que un piso externo no se
rechaza: preguntan acá. Un candado en un solo lugar es un candado; repetido en
tres, es tres oportunidades de que uno se olvide.
"""

ESTADOS = ("borrador", "aceptada", "aviso", "rechazada")
ORIGENES = ("inferida", "piso_externo", "curada")


class TransicionInvalida(Exception):
    """Se intentó una transición que la máquina de estados no permite."""


# Transiciones permitidas por origen, como conjunto de pares (desde, hasta).
# Se escriben explícitas, no por regla general, para que agregar un origen o un
# estado OBLIGUE a decidir sus transiciones en vez de heredar un default.
_COMUNES = frozenset(
    {
        ("borrador", "aceptada"),
        ("borrador", "rechazada"),
        ("aceptada", "aviso"),
        ("aviso", "aceptada"),
        ("aceptada", "rechazada"),
        ("aviso", "rechazada"),
    }
)

# El piso externo nace `aceptada` y NUNCA se rechaza (spec §5). Sólo se acota a
# aviso y se puede reactivar. No hay ni una transición a `rechazada`.
_PISO = frozenset(
    {
        ("aceptada", "aviso"),
        ("aviso", "aceptada"),
    }
)

_PERMITIDAS: dict[str, frozenset] = {
    "inferida": _COMUNES,
    "curada": _COMUNES,
    "piso_externo": _PISO,
}


def puede_transicionar(origin: str, from_status: str, to_status: str) -> bool:
    if to_status not in ESTADOS or from_status not in ESTADOS:
        return False
    return (from_status, to_status) in _PERMITIDAS.get(origin, frozenset())


def exigir_transicion(origin: str, from_status: str, to_status: str) -> None:
    """Levanta `TransicionInvalida` si la transición no está permitida."""
    if not puede_transicionar(origin, from_status, to_status):
        raise TransicionInvalida(
            f"una regla '{origin}' no puede pasar de {from_status!r} a "
            f"{to_status!r}"
        )
