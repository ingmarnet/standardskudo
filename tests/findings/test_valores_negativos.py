"""Valores negativos (Eje 4): un precio, peso o dimensión no puede ser negativo.

Determinista por construcción: un número menor que cero es imposible. El
sabotaje es la frontera del spec — el cero no entra acá (puede ser válido), y un
texto que no es número tampoco.
"""

from skudo.findings.catalog import MEDIA, Ficha, valores_negativos


def ficha(sku, attributes, visibility="4"):
    base = {"status": "1", "visibility": visibility}
    base.update(attributes)
    return Ficha(
        sku=sku, attributes=base,
        attribute_set_id=4, type_id="simple", categorias=(1,),
    )


def test_un_precio_negativo_se_marca():
    r = valores_negativos([ficha("a", {"price": "-50"})])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == MEDIA
    assert r.hallazgos[0].evidence["atributo"] == "price"


def test_un_peso_negativo_con_decimales_se_marca():
    r = valores_negativos([ficha("a", {"weight": "-1.5"})])
    assert len(r.hallazgos) == 1


def test_el_cero_no_es_negativo():
    # `0` puede ser válido (producto no físico): no entra acá.
    r = valores_negativos([ficha("a", {"weight": "0", "price": "0"})])
    assert r.hallazgos == []


def test_un_valor_positivo_o_un_texto_no_se_marcan():
    r = valores_negativos([
        ficha("a", {"price": "500000", "weight": "1.5", "name": "Paleta Nox"}),
    ])
    assert r.hallazgos == []


def test_dos_atributos_negativos_son_dos_hallazgos():
    r = valores_negativos([ficha("a", {"price": "-1", "weight": "-2"})])
    assert {h.evidence["atributo"] for h in r.hallazgos} == {"price", "weight"}


def test_la_variante_no_navegable_no_aplica_y_la_cobertura_suma():
    fichas = [
        ficha("a", {"price": "-1"}),
        ficha("v", {"price": "-1"}, visibility="1"),
    ]
    r = valores_negativos(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert len(r.hallazgos) == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
