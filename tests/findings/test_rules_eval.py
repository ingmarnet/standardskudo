from skudo.findings.catalog import CANDIDATO, MEDIA, Ficha
from skudo.findings.rules_eval import ReglaEvaluable, codigo_de, evaluar_regla

SETS = {"color": frozenset({4}), "peso": frozenset({4})}


def ficha(sku, **attrs):
    base = {"status": "1", "visibility": "4"}  # publicado
    base.update(attrs)
    return Ficha(sku=sku, attributes=base, attribute_set_id=4, type_id="simple")


def _regla(kind, attribute, **defn):
    return ReglaEvaluable(
        id=1, kind=kind, axis=3, severity=MEDIA if kind == "obligatoriedad" else CANDIDATO,
        scope_kind="attribute_set", scope_key="4", store_view=1,
        definition={"attribute": attribute, **defn},
    )


def test_obligatoriedad_marca_el_vacio_no_el_presente():
    regla = _regla("obligatoriedad", "color")
    fichas = [ficha("A", color="rojo"), ficha("B")]  # B no tiene color
    res = evaluar_regla(regla, fichas, SETS)
    skus = {h.subject_key for h in res.hallazgos}
    assert skus == {"B"}
    h = res.hallazgos[0]
    assert h.code == "regla:obligatoriedad:color"
    assert h.severity == MEDIA
    assert h.evidence["rule_id"] == 1
    assert res.cobertura.evaluados == 2  # A y B son evaluables (presente+vacio)


def test_desconocido_no_es_carencia_y_va_a_no_evaluado():
    regla = _regla("obligatoriedad", "color")
    # un producto sin attribute_set: su color es desconocido
    fichas = [Ficha(sku="X", attributes={"status": "1", "visibility": "4"},
                    attribute_set_id=None, type_id="simple")]
    res = evaluar_regla(regla, fichas, SETS)
    assert res.hallazgos == []
    assert res.cobertura.no_evaluado == 1
    assert res.cobertura.evaluados == 0


def test_rango_marca_fuera_de_limites_como_candidato():
    regla = _regla("rango", "peso", min=0.5, max=8.0)
    fichas = [ficha("A", peso="2.0"), ficha("B", peso="50")]  # B fuera
    res = evaluar_regla(regla, fichas, SETS)
    skus = {h.subject_key for h in res.hallazgos}
    assert skus == {"B"}
    assert res.hallazgos[0].severity == CANDIDATO
    assert res.hallazgos[0].evidence["valor"] == 50.0


def test_rango_ignora_el_no_numerico():
    regla = _regla("rango", "peso", min=0.5, max=8.0)
    fichas = [ficha("A", peso="liviano")]  # no numérico → no_aplica, no hallazgo
    res = evaluar_regla(regla, fichas, SETS)
    assert res.hallazgos == []
    assert res.cobertura.no_aplica == 1


def test_formato_sospecha_de_conversion_es_candidato():
    regla = _regla("formato", "peso", sospecha_conversion=True)
    fichas = [ficha("A", peso="2,5"), ficha("B", peso="3.0")]  # 2,5 ambiguo
    res = evaluar_regla(regla, fichas, SETS)
    assert any(h.subject_key == "A" for h in res.hallazgos)
    assert all(h.severity == CANDIDATO for h in res.hallazgos)


def test_fuera_de_scope_no_se_evalua():
    regla = _regla("obligatoriedad", "color")
    regla = ReglaEvaluable(**{**regla.__dict__, "scope_key": "9"})  # otro set
    fichas = [ficha("A")]  # set 4, la regla es del set 9
    res = evaluar_regla(regla, fichas, SETS)
    assert res.hallazgos == []
    assert res.cobertura.no_aplica == 1


def test_codigo_de_agrupa_por_atributo():
    assert codigo_de("obligatoriedad", "color") == "regla:obligatoriedad:color"
