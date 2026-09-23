"""Variantes que no comparten atributos de variación (Eje 1).

Un configurable varía por un conjunto de ejes (color, talle…). Si una de sus
variantes no informa el valor de uno de esos ejes, el selector del configurable
queda con un hueco. El detector marca al CONFIGURABLE (grupo) y lista las
variantes que faltan; un hallazgo por eje con hueco, no por variante.
"""

from skudo.findings.catalog import MEDIA, Ficha, variantes_sin_atributos_de_variacion


def ficha(sku, type_id="simple", parent_skus=(), ejes=(), attributes=None,
          visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility, **(attributes or {})},
        attribute_set_id=4,
        type_id=type_id,
        categorias=(1,),
        parent_skus=tuple(parent_skus),
        variation_attributes=tuple(ejes),
    )


def test_un_configurable_cuya_variante_omite_un_eje_se_marca():
    fichas = [
        ficha("PADRE", type_id="configurable", ejes=("color", "talle")),
        ficha("HIJO-T1", parent_skus=("PADRE",), attributes={"color": "rojo"}),
    ]
    r = variantes_sin_atributos_de_variacion(fichas)
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == MEDIA
    assert r.hallazgos[0].subject_type == "grupo"
    assert r.hallazgos[0].subject_key == "PADRE"
    assert r.hallazgos[0].evidence["atributo"] == "talle"
    assert r.hallazgos[0].evidence["skus"] == ["HIJO-T1"]


def test_un_configurable_cuyas_variantes_informan_todos_los_ejes_no_se_marca():
    fichas = [
        ficha("PADRE", type_id="configurable", ejes=("color",)),
        ficha("HIJO-T1", parent_skus=("PADRE",), attributes={"color": "rojo"}),
        ficha("HIJO-T2", parent_skus=("PADRE",), attributes={"color": "negro"}),
    ]
    r = variantes_sin_atributos_de_variacion(fichas)
    assert r.hallazgos == []


def test_un_configurable_sin_ejes_declarados_no_se_evalua():
    # Sin ejes no hay qué comparar: es otra rareza (un configurable sin
    # super_attribute), no la de este detector.
    fichas = [
        ficha("PADRE", type_id="configurable", ejes=()),
        ficha("HIJO-T1", parent_skus=("PADRE",)),
    ]
    r = variantes_sin_atributos_de_variacion(fichas)
    assert r.hallazgos == []
    assert r.cobertura.evaluados == 0
    assert r.cobertura.no_aplica == 2


def test_una_variante_deshabilitada_que_omite_el_eje_tambien_cuenta():
    # Igual que en `configurable_sin_hijos`: la variante existe aunque no esté
    # en vitrina, y su hueco en el selector sigue siendo un defecto del set.
    fichas = [
        ficha("PADRE", type_id="configurable", ejes=("talle",)),
        ficha("HIJO-T1", parent_skus=("PADRE",), status="2"),
    ]
    r = variantes_sin_atributos_de_variacion(fichas)
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].evidence["skus"] == ["HIJO-T1"]


def test_la_cobertura_suma():
    fichas = [
        ficha("PADRE", type_id="configurable", ejes=("color",)),
        ficha("HIJO", parent_skus=("PADRE",)),
        ficha("SIMPLE", visibility="1"),  # no navegable: no se evalúa
    ]
    r = variantes_sin_atributos_de_variacion(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 3
