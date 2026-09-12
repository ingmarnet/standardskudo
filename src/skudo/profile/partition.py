"""Descubrimiento de subtipos: qué división del attribute set dice algo.

Un subtipo no es una etiqueta que alguien escriba, es una partición que cambia
materialmente QUÉ ATRIBUTOS SE LLENAN. La medida de "dice algo" es la
ambigüedad: un atributo presente en el 50 % de un grupo no permite ninguna
regla —marcaría mal a la mitad—, y uno presente en el 97 % sí. Dividir bien es
convertir un 50/50 en dos grupos de 97/3.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from skudo.profile.coverage import Counts, ProductoPerfilado, coverage_vector

# --- Umbrales -------------------------------------------------------------
#
# Son constantes del código y NO configuración por tenant. Un umbral que se
# puede aflojar por configuración termina aflojado: es la misma lección que la
# válvula del barrido de S0, y aquí aflojarlo produce reglas que marcan como
# defecto la variedad legítima de un catálogo. Cambiarlos es editar esta línea
# y que se vea en el diff. Los valores de aquí son una HIPÓTESIS declarada, no
# una medición: la calibración contra el catálogo real del tenant piloto está
# pendiente (Task 9 del plan), y cuando ocurra dejará su cifra al lado de cada
# constante en `docs/superpowers/s1a-calibracion.md`.

# Productos mínimos en CADA grupo resultante. Por debajo, la cobertura del
# grupo es ruido: con 10 productos, un 90 % y un 100 % no se distinguen.
MIN_PARTICION = 50

# Cardinalidad máxima de un atributo para poder ser divisor. Un atributo con
# cincuenta valores no divide: pulveriza, y cada trozo cae bajo MIN_PARTICION.
MAX_CARD = 12

# Reducción mínima de ambigüedad para aceptar una división. Por debajo, lo que
# se ha encontrado es una fluctuación, y el precio de equivocarse es una regla
# de obligatoriedad aplicada a un grupo que no la cumple.
MIN_GANANCIA = 0.05


def ambiguity(vector: dict[str, Counts]) -> float | None:
    """Media de `min(c, 1-c)` sobre los atributos medibles del vector.

    Devuelve `None` si no hay ni un atributo medible: un grupo sobre el que no
    se puede afirmar nada no tiene ambigüedad baja, tiene ambigüedad
    desconocida, y son cosas distintas.
    """
    valores = [
        min(c.cobertura, 1.0 - c.cobertura)
        for c in vector.values()
        if c.cobertura is not None
    ]
    if not valores:
        return None
    return sum(valores) / len(valores)


def weighted_ambiguity(grupos: list[tuple[int, float | None]]) -> float | None:
    """Ambigüedad de una división, ponderada por el tamaño de cada grupo.

    Si CUALQUIER grupo no es medible, la división entera no lo es. La
    alternativa —ignorar ese grupo— hace que la ganancia aparente crezca cuanto
    más ignorancia haya, que es exactamente el incentivo al revés.
    """
    if not grupos:
        return None
    total = sum(n for n, _ in grupos)
    if total == 0:
        return None
    acumulado = 0.0
    for n, a in grupos:
        if a is None:
            return None
        acumulado += n * a
    return acumulado / total


def gain(antes: float | None, despues: float | None) -> float | None:
    """Cuánta ambigüedad quita la división. `None` si alguno de los dos falta."""
    if antes is None or despues is None:
        return None
    return antes - despues
# Etiquetas de los grupos que NO tienen valor de divisor. Son grupos de primera
# clase: los productos a los que les falta el propio divisor suelen ser los peor
# cargados del catálogo, o sea exactamente los que hay que medir.
SIN_VALOR = "(sin valor)"
SIN_CATEGORIA = "(sin categoria)"

# Sólo un atributo de valor único puede partir un conjunto. Un multiselect
# solapa grupos en vez de partirlos, y un producto acabaría contado dos veces.
INPUTS_DIVISIBLES = frozenset({"select", "boolean"})


@dataclass(frozen=True, order=True)
class Splitter:
    kind: str
    key: str


@dataclass(frozen=True)
class Choice:
    splitter: Splitter | None
    gain: float | None
    reason: str
    groups: dict[str, list[ProductoPerfilado]]


def split_value(
    product: ProductoPerfilado,
    splitter: Splitter,
    depth_by_category: dict[int, int],
) -> str:
    """En qué grupo cae un producto. Siempre devuelve un grupo: nadie se pierde."""
    if splitter.kind == "categoria":
        conocidas = [c for c in product.categorias if c in depth_by_category]
        if not conocidas:
            return SIN_CATEGORIA
        # La más profunda; empate por id menor, para que el resultado no dependa
        # del orden en que el espejo devolvió las asignaciones.
        elegida = min(conocidas, key=lambda c: (-depth_by_category[c], c))
        return str(elegida)
    valor = product.attributes.get(splitter.key)
    if valor is None or (isinstance(valor, str) and valor.strip() == ""):
        return SIN_VALOR
    return str(valor)


def split(
    products: Sequence[ProductoPerfilado],
    splitter: Splitter,
    depth_by_category: dict[int, int],
) -> dict[str, list[ProductoPerfilado]]:
    grupos: dict[str, list[ProductoPerfilado]] = defaultdict(list)
    for product in products:
        grupos[split_value(product, splitter, depth_by_category)].append(product)
    return dict(sorted(grupos.items()))


def candidate_splitters(
    products: Sequence[ProductoPerfilado],
    codes: Sequence[str],
    frontend_input_by_code: dict[str, str],
    depth_by_category: dict[int, int],
) -> list[Splitter]:
    """Los divisores que vale la pena probar, en orden estable."""
    candidatos: list[Splitter] = []
    for code in sorted(codes):
        if frontend_input_by_code.get(code) not in INPUTS_DIVISIBLES:
            continue
        valores = {
            v
            for v in (
                split_value(p, Splitter("atributo", code), depth_by_category)
                for p in products
            )
            if v != SIN_VALOR
        }
        if 2 <= len(valores) <= MAX_CARD:
            candidatos.append(Splitter("atributo", code))
    por_categoria = {
        v
        for v in (
            split_value(p, Splitter("categoria", "categoria"), depth_by_category)
            for p in products
        )
        if v != SIN_CATEGORIA
    }
    if 2 <= len(por_categoria) <= MAX_CARD:
        candidatos.append(Splitter("categoria", "categoria"))
    return sorted(candidatos)


def choose_splitter(
    products: Sequence[ProductoPerfilado],
    codes: Sequence[str],
    sets_by_code: dict[str, frozenset[int]],
    frontend_input_by_code: dict[str, str],
    depth_by_category: dict[int, int],
) -> Choice:
    """El divisor que más ambigüedad quita, si quita bastante y no pulveriza.

    La razón viaja con la decisión. Un perfil que sólo dice "sin subtipo" no
    permite discutir nada; uno que dice "ganancia insuficiente: 0,02" permite
    mirar el umbral y decidir si el problema es el catálogo o la constante.
    """
    base = ambiguity(coverage_vector(products, codes, sets_by_code))
    candidatos = candidate_splitters(
        products, codes, frontend_input_by_code, depth_by_category
    )
    if not candidatos:
        return Choice(None, None, "sin candidatos", {})

    mejor: Choice | None = None
    hubo_grupo_pequeno = False
    for splitter in candidatos:
        grupos = split(products, splitter, depth_by_category)
        if min(len(g) for g in grupos.values()) < MIN_PARTICION:
            hubo_grupo_pequeno = True
            continue
        ponderada = weighted_ambiguity(
            [
                (len(g), ambiguity(coverage_vector(g, codes, sets_by_code)))
                for _, g in sorted(grupos.items())
            ]
        )
        ganancia = gain(base, ponderada)
        if ganancia is None:
            continue
        # Empate: gana el primero del orden estable de `candidatos`, no el
        # último visto. Sin este `>` estricto, el resultado dependería del orden
        # de iteración y dos pasadas iguales podrían diferir.
        if mejor is None or ganancia > mejor.gain:
            mejor = Choice(splitter, ganancia, "elegido", grupos)

    if mejor is None:
        return Choice(None, None, "grupo pequeno" if hubo_grupo_pequeno else "sin candidatos", {})
    if mejor.gain < MIN_GANANCIA:
        return Choice(None, mejor.gain, "ganancia insuficiente", {})
    return mejor
