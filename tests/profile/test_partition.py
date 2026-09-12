import pytest

from skudo.profile.coverage import ProductoPerfilado
from skudo.profile.partition import (
    SIN_CATEGORIA,
    SIN_VALOR,
    Splitter,
    candidate_splitters,
    choose_splitter,
    split,
    split_value,
)

SETS = {c: frozenset({4}) for c in ("tipo", "talle", "voltaje", "color", "etiquetas")}
CODES = ("color", "etiquetas", "talle", "tipo", "voltaje")
INPUTS = {
    "tipo": "select",
    "talle": "select",
    "voltaje": "select",
    "color": "select",
    "etiquetas": "multiselect",
}
PROFUNDIDAD = {10: 1, 20: 2, 21: 2}


def p(sku, **attrs):
    cats = attrs.pop("_cats", ())
    return ProductoPerfilado(
        sku=sku, attributes=attrs, attribute_set_id=4, categorias=cats
    )


def catalogo(n_ropa=60, n_electro=60):
    """Un set que mezcla dos fichas: la ropa llena `talle`, el electro `voltaje`.

    El color va alternado a propósito, para que exista un candidato a divisor
    que NO informa de nada: si el perfilador eligiera por orden en vez de por
    ganancia, se quedaría con él. Y `etiquetas` lleva valores de verdad —dos,
    dentro del rango de cardinalidad admisible— para que lo ÚNICO que lo deje
    fuera de los candidatos sea su tipo de entrada. Sin valores, el test del
    multiselect pasaría por la razón equivocada: la de un atributo que nadie usa.
    """
    ropa = [
        p(
            f"r{i}",
            tipo="ropa",
            talle="M",
            color="negro" if i % 2 else "blanco",
            etiquetas="oferta" if i % 3 else "nuevo",
        )
        for i in range(n_ropa)
    ]
    electro = [
        p(
            f"e{i}",
            tipo="electro",
            voltaje="220",
            color="negro" if i % 2 else "blanco",
            etiquetas="oferta" if i % 3 else "nuevo",
        )
        for i in range(n_electro)
    ]
    return ropa + electro


def test_un_multiselect_no_es_candidato_a_divisor():
    """Un producto puede tener tres etiquetas a la vez: no parte el conjunto,
    lo solapa. Dividir por él pondría el mismo producto en dos grupos."""
    candidatos = candidate_splitters(catalogo(), CODES, INPUTS, PROFUNDIDAD)
    assert Splitter("atributo", "etiquetas") not in candidatos


def test_un_atributo_de_valor_unico_no_es_candidato():
    productos = [p(f"x{i}", tipo="ropa") for i in range(60)]
    assert Splitter("atributo", "tipo") not in candidate_splitters(
        productos, CODES, INPUTS, PROFUNDIDAD
    )


def test_un_atributo_demasiado_variado_no_es_candidato():
    productos = [p(f"x{i}", color=f"c{i}") for i in range(60)]
    assert Splitter("atributo", "color") not in candidate_splitters(
        productos, CODES, INPUTS, PROFUNDIDAD
    )


def test_los_candidatos_van_ordenados():
    candidatos = candidate_splitters(catalogo(), CODES, INPUTS, PROFUNDIDAD)
    assert candidatos == sorted(candidatos, key=lambda s: (s.kind, s.key))


def test_los_productos_sin_valor_forman_su_propio_grupo():
    """Descartarlos sesgaría la medición hacia los productos bien cargados, que
    son precisamente los que no hace falta analizar."""
    productos = [p(f"a{i}", tipo="ropa") for i in range(3)] + [p("b1")]
    grupos = split(productos, Splitter("atributo", "tipo"), PROFUNDIDAD)
    assert sorted(grupos) == [SIN_VALOR, "ropa"]
    assert [x.sku for x in grupos[SIN_VALOR]] == ["b1"]


def test_la_categoria_divisora_es_la_mas_profunda():
    producto = p("x", _cats=(10, 20))
    assert split_value(producto, Splitter("categoria", "categoria"), PROFUNDIDAD) == "20"


def test_el_empate_de_profundidad_se_rompe_por_id():
    producto = p("x", _cats=(21, 20))
    assert split_value(producto, Splitter("categoria", "categoria"), PROFUNDIDAD) == "20"


def test_un_producto_sin_categoria_tiene_su_grupo():
    assert (
        split_value(p("x"), Splitter("categoria", "categoria"), PROFUNDIDAD)
        == SIN_CATEGORIA
    )


def test_elige_el_divisor_que_separa_las_dos_fichas():
    eleccion = choose_splitter(catalogo(), CODES, SETS, INPUTS, PROFUNDIDAD)
    assert eleccion.splitter == Splitter("atributo", "tipo")
    assert eleccion.reason == "elegido"
    assert eleccion.gain == pytest.approx(0.2)
    assert sorted(eleccion.groups) == ["electro", "ropa"]


def test_un_set_homogeneo_declara_que_no_hay_subtipo():
    """Decir que NO hay subtipo vale tanto como encontrarlo: distingue un set
    homogéneo de uno que nadie supo dividir."""
    productos = [p(f"x{i}", tipo="ropa", talle="M") for i in range(120)]
    eleccion = choose_splitter(productos, CODES, SETS, INPUTS, PROFUNDIDAD)
    assert eleccion.splitter is None
    assert eleccion.reason in {"sin candidatos", "ganancia insuficiente"}


def test_una_division_que_deja_un_grupo_pequeno_se_rechaza():
    """El divisor informativo existe y separa bien, pero un lado tiene cinco
    productos: con eso, su cobertura es ruido y la regla que saldría sería
    ruido con aspecto de norma."""
    ropa = [p(f"r{i}", tipo="ropa", talle="M", color="negro") for i in range(120)]
    electro = [p(f"e{i}", tipo="electro", voltaje="220", color="negro") for i in range(5)]
    eleccion = choose_splitter(ropa + electro, CODES, SETS, INPUTS, PROFUNDIDAD)
    assert eleccion.splitter is None
    assert eleccion.reason == "grupo pequeno"


def test_la_eleccion_es_determinista_ante_el_empate():
    """Dos divisores con la misma ganancia: gana el primero por orden
    alfabético, siempre, aunque la lista de productos llegue barajada."""
    izquierda = [p(f"a{i}", talle="M", tipo="ropa", color="negro") for i in range(60)]
    derecha = [p(f"b{i}", voltaje="220", tipo="electro", color="blanco") for i in range(60)]
    directo = choose_splitter(izquierda + derecha, CODES, SETS, INPUTS, PROFUNDIDAD)
    invertido = choose_splitter(derecha + izquierda, CODES, SETS, INPUTS, PROFUNDIDAD)
    assert directo.splitter == invertido.splitter
    assert directo.splitter.key == "color"
    assert directo.gain == pytest.approx(invertido.gain)
