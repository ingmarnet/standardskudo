"""Unidades ausentes (Eje 3).

La otra mitad de «unidades ausentes o mezcladas»: un valor numérico sin unidad
dentro de un atributo que en otras filas SÍ la lleva. Un atributo donde nada
lleva unidad es unitless por naturaleza y no se toca.
"""

from skudo.findings.catalog import BAJA, Ficha, unidades_ausentes


def ficha(sku, attributes=None, visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility, **(attributes or {})},
        attribute_set_id=4,
        type_id="simple",
        categorias=(1,),
    )


def test_un_numero_sin_unidad_junto_a_otro_con_unidad_se_marca():
    r = unidades_ausentes([
        ficha("A", {"peso": "1 kg"}),
        ficha("B", {"peso": "500"}),
    ])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == BAJA
    assert r.hallazgos[0].subject_key == "peso"
    assert r.hallazgos[0].evidence["skus"] == ["B"]
    assert r.hallazgos[0].evidence["unidades"] == ["kg"]


def test_un_atributo_unitless_por_naturaleza_no_se_marca():
    r = unidades_ausentes([
        ficha("A", {"talle": "40"}),
        ficha("B", {"talle": "42"}),
        ficha("C", {"talle": "44"}),
    ])
    assert r.hallazgos == []


def test_un_atributo_con_solo_unidades_no_se_marca():
    r = unidades_ausentes([
        ficha("A", {"peso": "500 g"}),
        ficha("B", {"peso": "1 kg"}),
    ])
    assert r.hallazgos == []


def test_un_no_numerico_no_se_cuenta_como_sin_unidad():
    # "N/A" no es un número al que le falte unidad: es basura, otro detector.
    r = unidades_ausentes([
        ficha("A", {"peso": "1 kg"}),
        ficha("B", {"peso": "N/A"}),
    ])
    assert r.hallazgos == []


def test_un_atributo_sin_numeros_no_se_marca():
    r = unidades_ausentes([
        ficha("A", {"color": "rojo"}),
        ficha("B", {"color": "azul"}),
    ])
    assert r.hallazgos == []


def test_la_cobertura_suma():
    fichas = [
        ficha("A", {"peso": "1 kg"}),
        ficha("B", {"peso": "500"}),
        ficha("C", visibility="1"),  # no navegable: no se evalúa
    ]
    r = unidades_ausentes(fichas)
    c = r.cobertura
    assert c.evaluados == 2
    assert c.evaluados + c.no_aplica + c.no_evaluado == 3
