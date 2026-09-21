"""Filter-blind (Eje 3): un atributo FILTRABLE vacío donde aplica esconde el
producto de la navegación por filtros. El hallazgo con traducción más directa
a dinero perdido (spec §6.1, eje 3)."""

from skudo.findings.catalog import MEDIA, Ficha
from skudo.findings.filter_blind import codigo_de, evaluar_filtro_ciego

# color aplica al set 4; talla, al 4 y 9.
SETS = {"color": frozenset({4}), "talla": frozenset({4, 9})}
PUB = {"status": "1", "visibility": "4"}


def _f(sku, sid, extra):
    return Ficha(sku=sku, attributes={**PUB, **extra}, attribute_set_id=sid)


def _por_codigo(resultados):
    return {r.cobertura.detector: r for r in resultados}


def test_atributo_filtrable_vacio_donde_aplica_es_filtro_ciego():
    fichas = [
        _f("A", 4, {"color": "rojo"}),   # ok
        _f("B", 4, {}),                  # color vacío -> filtro ciego
    ]
    res = _por_codigo(evaluar_filtro_ciego(fichas, {"color"}, SETS))
    r = res[codigo_de("color")]
    assert [h.subject_key for h in r.hallazgos] == ["B"]
    assert r.hallazgos[0].severity == MEDIA
    assert r.hallazgos[0].axis == 3
    assert r.hallazgos[0].evidence == {"attribute": "color"}
    assert r.cobertura.evaluados == 2  # A y B están en scope (set 4)


def test_no_marca_donde_el_atributo_no_aplica_al_set():
    fichas = [_f("C", 9, {})]  # color no aplica al set 9
    res = _por_codigo(evaluar_filtro_ciego(fichas, {"color"}, SETS))
    r = res[codigo_de("color")]
    assert r.hallazgos == []
    assert r.cobertura.no_aplica == 1
    assert r.cobertura.evaluados == 0


def test_no_marca_productos_no_publicados():
    fichas = [Ficha(sku="D", attributes={"status": "2", "visibility": "4"},
                    attribute_set_id=4)]  # deshabilitado
    res = _por_codigo(evaluar_filtro_ciego(fichas, {"color"}, SETS))
    r = res[codigo_de("color")]
    assert r.hallazgos == []
    assert r.cobertura.no_aplica == 1


def test_set_desconocido_no_confunde_vacio_con_desconocido():
    # attribute_set_id None -> no sabemos si aplica -> DESCONOCIDO -> no_evaluado,
    # NUNCA un filtro-ciego fabricado (la ignorancia gana).
    fichas = [Ficha(sku="E", attributes={**PUB}, attribute_set_id=None)]
    res = _por_codigo(evaluar_filtro_ciego(fichas, {"color"}, SETS))
    r = res[codigo_de("color")]
    assert r.hallazgos == []
    assert r.cobertura.no_evaluado == 1


def test_un_atributo_en_dos_sets_no_duplica_codigo():
    # talla aplica a 4 y 9: un solo Resultado (un solo código) que cubre ambos.
    fichas = [_f("A", 4, {}), _f("B", 9, {})]
    res = evaluar_filtro_ciego(fichas, {"talla"}, SETS)
    codigos = [r.cobertura.detector for r in res]
    assert codigos == [codigo_de("talla")]  # uno solo, sin colisión
    assert {h.subject_key for h in res[0].hallazgos} == {"A", "B"}
