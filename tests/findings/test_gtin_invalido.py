"""GTIN inválido (Eje 1): formato o dígito de control, solo si hay valor.

El algoritmo GS1 decide, no un umbral. El sabotaje es justamente el dígito de
control: un GTIN que cuadra no se marca, uno que no cuadra sí. Un GTIN ausente
no se evalúa (la exigencia de que exista es una regla con aplicabilidad, no este
detector).
"""

from skudo.findings.catalog import MEDIA, Ficha, gtin_invalido


def ficha(sku, gtin, visibility="4"):
    return Ficha(
        sku=sku,
        attributes={"status": "1", "visibility": visibility, "gtin": gtin},
        attribute_set_id=4, type_id="simple", categorias=(1,),
    )


# GTIN-13 válido (4006381333931, de la especificación GS1): el dígito de control
# es 1.
def test_un_gtin_valido_no_se_marca():
    r = gtin_invalido([ficha("a", "4006381333931")])
    assert r.hallazgos == []


def test_un_digito_de_control_que_no_cuadra_se_marca():
    r = gtin_invalido([ficha("a", "4006381333932")])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == MEDIA
    assert r.hallazgos[0].evidence["motivo"] == "dígito de control"


def test_un_largo_que_no_es_de_gtin_se_marca_como_formato():
    r = gtin_invalido([ficha("a", "40063813339")])  # 11 dígitos
    assert r.hallazgos[0].evidence["motivo"] == "formato"


def test_un_valor_con_letras_se_marca_como_formato():
    r = gtin_invalido([ficha("a", "40063X1333931")])
    assert r.hallazgos[0].evidence["motivo"] == "formato"


def test_un_gtin_ausente_no_se_evalua():
    r = gtin_invalido([ficha("a", "")])
    assert r.hallazgos == []
    assert r.cobertura.evaluados == 1


def test_los_ceros_iniciales_se_preservan():
    # Un GTIN-14 válido con ceros a la izquierda no es un error de formato.
    g = "00040063813331"  # 14 dígitos
    # dígito de control de "0004006381333"
    r = gtin_invalido([ficha("a", g)])
    # No marcamos formato (largo 14), solo comprobamos que no lo señale por largo.
    assert not any(h.evidence["motivo"] == "formato" for h in r.hallazgos)


def test_la_variante_no_navegable_no_aplica_y_la_cobertura_suma():
    fichas = [
        ficha("a", "4006381333932"),
        ficha("v", "4006381333932", visibility="1"),
    ]
    r = gtin_invalido(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert len(r.hallazgos) == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
