"""Unidades mezcladas dentro del mismo atributo (Eje 3).

Un atributo que mezcla unidades (kg y g, l y ml) vuelve incomparables sus
valores. El detector marca al ATRIBUTO (no al producto) cuando dos o más
unidades conviven en sus valores; un hallazgo por atributo.
"""

from skudo.findings.catalog import BAJA, Ficha, unidades_mezcladas


def ficha(sku, attributes=None, visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility, **(attributes or {})},
        attribute_set_id=4,
        type_id="simple",
        categorias=(1,),
    )


def test_un_atributo_que_mezcla_kg_y_g_se_marca():
    fichas = [
        ficha("A", {"peso": "1.5 kg"}),
        ficha("B", {"peso": "500 g"}),
    ]
    r = unidades_mezcladas(fichas)
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == BAJA
    assert r.hallazgos[0].subject_type == "atributo"
    assert r.hallazgos[0].subject_key == "peso"
    assert r.hallazgos[0].evidence["unidades"] == ["g", "kg"]


def test_un_atributo_con_una_sola_unidad_no_se_marca():
    fichas = [
        ficha("A", {"peso": "1.5 kg"}),
        ficha("B", {"peso": "2 kg"}),
    ]
    r = unidades_mezcladas(fichas)
    assert r.hallazgos == []


def test_la_unidad_mayuscula_equivale_a_la_minuscula():
    # "KG" y "kg" son la misma unidad: no es mezcla, es grafía.
    fichas = [
        ficha("A", {"peso": "1.5 KG"}),
        ficha("B", {"peso": "2 kg"}),
    ]
    r = unidades_mezcladas(fichas)
    assert r.hallazgos == []


def test_una_talla_no_se_lee_como_unidad():
    # "L" y "M" solas (sin número) no llevan la unidad: son talles, no litros ni
    # metros. El número adelante es lo que desambigua.
    fichas = [
        ficha("A", {"talle": "M"}),
        ficha("B", {"talle": "L"}),
    ]
    r = unidades_mezcladas(fichas)
    assert r.hallazgos == []


def test_un_texto_con_unidad_no_reconocida_no_cuenta():
    # `pack`, `x` de dimensiones o palabras sueltas no son unidades físicas: se
    # ignoran y no producen una mezcla falsa.
    fichas = [
        ficha("A", {"unidades": "5 pack"}),
        ficha("B", {"unidades": "10 pack"}),
    ]
    r = unidades_mezcladas(fichas)
    assert r.hallazgos == []


def test_un_atributo_sin_unidad_en_ningun_valor_no_se_marca():
    fichas = [
        ficha("A", {"peso": "1500"}),
        ficha("B", {"peso": "2000"}),
    ]
    r = unidades_mezcladas(fichas)
    assert r.hallazgos == []


def test_la_cobertura_suma():
    fichas = [
        ficha("A", {"peso": "1.5 kg"}),
        ficha("B", {"peso": "500 g"}),
        ficha("C", visibility="1"),  # no navegable: no se evalúa
    ]
    r = unidades_mezcladas(fichas)
    c = r.cobertura
    assert c.evaluados == 2
    assert c.evaluados + c.no_aplica + c.no_evaluado == 3
