"""`duplicados`: candidatos a duplicado por nombre CASI idéntico.

Cada exclusión de este archivo es la frontera que lo separa de los dos
detectores que ya existen: `nombres_repetidos` (nombre EXACTO) y
`variantes_por_talle` (diferencia que es sólo talle). Un detector que se pisa
con ellos cuenta la misma causa dos veces, que es lo que el spec prohíbe.
"""

from skudo.findings.catalog import CANDIDATO, Ficha, duplicado


def ficha(sku, *, nombre, status="1", visibility="4", tipo="simple"):
    return Ficha(
        sku=sku,
        attributes={"name": nombre, "status": status, "visibility": visibility},
        type_id=tipo,
        categorias=(1,),
    )


def test_nombres_casi_identicos_son_un_candidato():
    """«rojo» por «roja»: la misma cosa con una errata de género. Un candidato,
    nunca un veredicto — confirmar equivalencia exige mirar el color de verdad."""
    r = duplicado([
        ficha("a", nombre="Silla Gamer Roja"),
        ficha("b", nombre="Silla Gamer Rojo"),
    ])
    assert len(r.hallazgos) == 1
    h = r.hallazgos[0]
    assert h.code == "duplicado"
    assert h.severity == CANDIDATO
    assert h.axis == 1
    assert h.evidence["productos"] == 2
    assert h.evidence["skus"] == ["a", "b"], (
        "el score atribuye el hallazgo a CADA miembro: si la lista se trunca, "
        "hay productos con la nota intacta"
    )
    assert h.evidence["truncado"] is False


def test_un_nombre_exacto_repetido_no_se_reconsidera():
    """«Roja»×2 y «Rojo»×2 ya los marca `nombres_repetidos` (cada grupo por su
    lado). Si `duplicado` además los vincula por parecerse, esos cuatro
    productos descuentan DOS veces por la misma causa — la doble penalización
    que el spec prohíbe."""
    r = duplicado([
        ficha("a1", nombre="Silla Gamer Roja"),
        ficha("a2", nombre="Silla Gamer Roja"),
        ficha("b1", nombre="Silla Gamer Rojo"),
        ficha("b2", nombre="Silla Gamer Rojo"),
    ])
    assert r.hallazgos == []


def test_una_diferencia_solo_de_talle_no_es_duplicado():
    """Dos talles del mismo modelo son la familia de `variantes_por_talle`, no
    un duplicado."""
    r = duplicado([
        ficha("a", nombre="ZAPATILLA MIZUNO WAVE EXCEED 5,5'"),
        ficha("b", nombre="ZAPATILLA MIZUNO WAVE EXCEED 6'"),
    ])
    assert r.hallazgos == []


def test_nombres_muy_distintos_no_se_marcan():
    r = duplicado([
        ficha("a", nombre="Paleta Bullpadel"),
        ficha("b", nombre="Raqueta de Tenis"),
    ])
    assert r.hallazgos == []


def test_nombres_demasiado_cortos_no_agrupan():
    """«mesa roja»/«mesa rojo» comparten casi todo, pero lo que comparten es la
    categoría, no el producto."""
    r = duplicado([
        ficha("a", nombre="mesa roja"),
        ficha("b", nombre="mesa rojo"),
    ])
    assert r.hallazgos == []


def test_una_cadena_de_similares_es_UN_grupo():
    """a~b y b~c forman un solo componente: un hallazgo, no tres pares."""
    r = duplicado([
        ficha("a", nombre="Silla Gamer Roja"),
        ficha("b", nombre="Silla Gamer Rojo"),
        ficha("c", nombre="Silla Gamer Roje"),
    ])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].evidence["productos"] == 3


def test_solo_los_publicados_se_evaluan():
    r = duplicado([
        ficha("a", nombre="Silla Gamer Roja"),
        ficha("b", nombre="Silla Gamer Rojo", visibility="1"),
    ])
    assert r.hallazgos == []
    assert r.cobertura.evaluados == 1
    assert r.cobertura.no_aplica == 1
