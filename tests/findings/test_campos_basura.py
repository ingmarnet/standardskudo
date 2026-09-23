"""Valores basura (Eje 3): un atributo con comodín de carga en vez de valor.

Todo objetivable con un set de tokens: no es candidato, es defecto real de baja
severidad. El sabotaje es la frontera del spec — `0` no es basura, y un comodín
embebido dentro de un valor real tampoco.
"""

from skudo.findings.catalog import BAJA, Ficha, campos_basura


def ficha(sku, attributes, visibility="4"):
    base = {"status": "1", "visibility": visibility}
    base.update(attributes)
    return Ficha(
        sku=sku, attributes=base,
        attribute_set_id=4, type_id="simple", categorias=(1,),
    )


def test_los_comodines_del_spec_se_marcan_por_atributo():
    r = campos_basura([ficha("a", {"material": "N/A", "color": "-"})])
    atributos = {h.evidence["atributo"] for h in r.hallazgos}
    assert atributos == {"material", "color"}
    assert r.hallazgos[0].severity == BAJA


def test_sin_dato_y_xx_se_marcan_sin_importar_la_caja():
    r = campos_basura([ficha("a", {"a": "SIN DATO", "b": "XX"})])
    assert {h.evidence["atributo"] for h in r.hallazgos} == {"a", "b"}


def test_el_cero_no_es_basura():
    # `0` es un valor presente, no un comodín: se queda.
    r = campos_basura([ficha("a", {"peso": "0"})])
    assert r.hallazgos == []


def test_un_valor_real_que_contiene_un_token_no_se_marca():
    # «N/A» embebido en un texto real es otra cosa, no un campo basura.
    r = campos_basura([ficha("a", {"obs": "Apto N/A menores"})])
    assert r.hallazgos == []


def test_el_valor_vacio_no_es_basura():
    # Vacío es carencia (otro detector), no comodín.
    r = campos_basura([ficha("a", {"material": ""})])
    assert r.hallazgos == []


def test_la_variante_no_navegable_no_aplica_y_la_cobertura_suma():
    fichas = [
        ficha("a", {"material": "SIN DATO"}),
        ficha("v", {"material": "SIN DATO"}, visibility="1"),
    ]
    r = campos_basura(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert len(r.hallazgos) == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
