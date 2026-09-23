"""Texto duplicado en masa (Eje 5).

Una misma descripción pegada en decenas de productos es la firma del
copy-paste de carga: no distingue el producto y diluye el contenido. Solo se
marca a partir del umbral; por debajo puede ser una familia legítima.
"""

from skudo.findings.catalog import (
    CANDIDATO,
    UMBRAL_TEXTO_DUPLICADO,
    Ficha,
    texto_duplicado,
)


def ficha(sku, description=None, visibility="4", status="1"):
    attrs = {"status": status, "visibility": visibility}
    if description is not None:
        attrs["description"] = description
    return Ficha(
        sku=sku,
        attributes=attrs,
        attribute_set_id=4,
        type_id="simple",
        categorias=(1,),
    )


def _n(sku, texto, n):
    return [ficha(f"{sku}-{i}", texto) for i in range(n)]


def test_un_texto_compartido_por_muchos_se_marca():
    fichas = _n("P", "Pala de pádel profesional", UMBRAL_TEXTO_DUPLICADO)
    r = texto_duplicado(fichas)
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == CANDIDATO
    assert r.hallazgos[0].subject_type == "grupo"
    assert r.hallazgos[0].evidence["productos"] == UMBRAL_TEXTO_DUPLICADO


def test_por_debajo_del_umbral_no_se_marca():
    fichas = _n("P", "Pala de pádel profesional", UMBRAL_TEXTO_DUPLICADO - 1)
    assert texto_duplicado(fichas).hallazgos == []


def test_dos_textos_distintos_no_se_mezclan():
    fichas = _n("A", "Pala de pádel", UMBRAL_TEXTO_DUPLICADO)
    fichas += _n("B", "Zapatilla running", UMBRAL_TEXTO_DUPLICADO)
    r = texto_duplicado(fichas)
    assert len(r.hallazgos) == 2


def test_la_descripcion_vacia_no_se_agrupa():
    fichas = _n("P", None, UMBRAL_TEXTO_DUPLICADO)
    assert texto_duplicado(fichas).hallazgos == []


def test_un_no_navegable_no_entra_en_el_grupo():
    fichas = _n("P", "Texto", UMBRAL_TEXTO_DUPLICADO - 1)
    fichas.append(ficha("oculto", "Texto", visibility="1"))
    assert texto_duplicado(fichas).hallazgos == []


def test_la_cobertura_suma():
    fichas = _n("P", "Texto", UMBRAL_TEXTO_DUPLICADO) + [ficha("B", visibility="1")]
    r = texto_duplicado(fichas)
    c = r.cobertura
    assert c.evaluados == UMBRAL_TEXTO_DUPLICADO
    assert c.evaluados + c.no_aplica + c.no_evaluado == UMBRAL_TEXTO_DUPLICADO + 1
