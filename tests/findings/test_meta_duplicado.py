"""Meta title o description duplicados (Eje 9).

El mismo meta title en decenas de productos es un aviso clásico de Search
Console. Solo se marca a partir del umbral; por debajo puede ser una familia
o un template legítimo.
"""

from skudo.findings.catalog import (
    CANDIDATO,
    UMBRAL_META_DUPLICADO,
    Ficha,
    meta_duplicado,
)


def ficha(sku, attributes=None, visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility, **(attributes or {})},
        attribute_set_id=4,
        type_id="simple",
        categorias=(1,),
    )


def _n(sku, atributo, valor, n):
    return [ficha(f"{sku}-{i}", {atributo: valor}) for i in range(n)]


def test_un_meta_title_compartido_por_muchos_se_marca():
    fichas = _n("P", "meta_title", "Pala de pádel", UMBRAL_META_DUPLICADO)
    r = meta_duplicado(fichas)
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == CANDIDATO
    assert r.hallazgos[0].evidence["atributo"] == "meta_title"
    assert r.hallazgos[0].evidence["productos"] == UMBRAL_META_DUPLICADO


def test_por_debajo_del_umbral_no_se_marca():
    fichas = _n("P", "meta_title", "Pala de pádel", UMBRAL_META_DUPLICADO - 1)
    assert meta_duplicado(fichas).hallazgos == []


def test_title_y_description_se_verifican_por_separado():
    fichas = _n("A", "meta_title", "Título", UMBRAL_META_DUPLICADO)
    fichas += _n("B", "meta_description", "Descripción", UMBRAL_META_DUPLICADO)
    r = meta_duplicado(fichas)
    assert len(r.hallazgos) == 2
    assert {h.evidence["atributo"] for h in r.hallazgos} == {
        "meta_title", "meta_description",
    }


def test_el_mismo_texto_en_dos_atributos_distintos_no_se_mezcla():
    fichas = _n("A", "meta_title", "Igual", UMBRAL_META_DUPLICADO)
    fichas += _n("B", "meta_description", "Igual", UMBRAL_META_DUPLICADO)
    r = meta_duplicado(fichas)
    assert len(r.hallazgos) == 2


def test_el_valor_vacio_no_se_agrupa():
    # La ausencia es `sin_meta_title`, no un duplicado.
    assert meta_duplicado([ficha("A", {"meta_title": ""})]).hallazgos == []


def test_la_cobertura_suma():
    fichas = _n("P", "meta_title", "Título", UMBRAL_META_DUPLICADO)
    fichas.append(ficha("B", visibility="1"))
    r = meta_duplicado(fichas)
    c = r.cobertura
    assert c.evaluados == UMBRAL_META_DUPLICADO
    assert c.evaluados + c.no_aplica + c.no_evaluado == UMBRAL_META_DUPLICADO + 1
