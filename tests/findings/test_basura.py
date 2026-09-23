"""Basura en el nombre (Eje 1): HTML, espacios dobles, control, signos, SKU.

Todo objetivable con un regex, así que no es candidato: es defecto real de baja
severidad. El sabotaje: un nombre limpio no se marca, un SKU que es una palabra
(no un código) no se marca, y un nombre que ES el sku es el detector vecino, no
un SKU embebido.
"""

from skudo.findings.catalog import BAJA, Ficha, nombre_con_basura


def ficha(sku, name, set_id=4):
    return Ficha(
        sku=sku,
        attributes={"name": name, "status": "1", "visibility": "4"},
        attribute_set_id=set_id, type_id="simple", categorias=(1,),
    )


def test_el_html_y_los_espacios_dobles_se_marcan():
    r = nombre_con_basura([ficha("a", "Paleta <b>Nox</b> &nbsp; Pro")])
    assert len(r.hallazgos) == 1
    assert "html" in r.hallazgos[0].evidence["motivo"]

    r = nombre_con_basura([ficha("b", "Paleta  Nox")])  # dos espacios
    assert r.hallazgos[0].evidence["motivo"] == ["espacios"]


def test_el_caracter_de_control_y_la_puntuacion_se_marcan():
    r = nombre_con_basura([ficha("a", "Paleta\x07Nox")])
    assert r.hallazgos[0].evidence["motivo"] == ["control"]

    r = nombre_con_basura([ficha("b", "PALETA NUEVO!!!")])
    assert r.hallazgos[0].evidence["motivo"] == ["puntuacion"]


def test_el_sku_embebido_se_marca_pero_la_marca_no():
    # "INV-1234" es un código (lleva dígito) metido en el nombre.
    r = nombre_con_basura([ficha("INV-1234", "Paleta INV-1234 Pro")])
    assert "sku" in r.hallazgos[0].evidence["motivo"]

    # "Nox" es una palabra, no un código: no es basura.
    r = nombre_con_basura([ficha("Nox", "Paleta Nox Pro")])
    assert r.hallazgos == []


def test_un_nombre_limpio_no_se_marca():
    r = nombre_con_basura([ficha("a", "Paleta Nox ML10 Pro 2024")])
    assert r.hallazgos == []


def test_un_nombre_que_es_el_sku_no_es_sku_embebido():
    # «INV-1234» como nombre ES el código: es el detector vecino («el nombre es
    # un código»), no un SKU embebido dentro de un nombre.
    r = nombre_con_basura([ficha("INV-1234", "INV-1234")])
    assert "sku" not in (r.hallazgos[0].evidence["motivo"] if r.hallazgos else [])


def test_la_severidad_es_baja_y_la_cobertura_suma():
    fichas = [
        ficha("a", "Paleta  Nox"),
        Ficha(sku="v", attributes={"name": "v", "status": "1", "visibility": "1"},
              attribute_set_id=4, type_id="simple", categorias=(1,)),
    ]
    r = nombre_con_basura(fichas)
    assert r.hallazgos[0].severity == BAJA
    c = r.cobertura
    assert c.evaluados == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
