"""El nombre es un código (Eje 1): el storefront muestra el SKU, no un nombre.

Determinista por construcción: solo nombre == sku. El sabotaje es justamente
ese límite — si el detector empieza a adivinar «parece un código», marca modelos
legítimos (`CBR600RR`) que no se parecen a su sku.
"""

from skudo.findings.catalog import MEDIA, Ficha, nombre_es_codigo


def ficha(sku, name, set_id=4, visibility="4"):
    return Ficha(
        sku=sku,
        attributes={"name": name, "status": "1", "visibility": visibility},
        attribute_set_id=set_id, type_id="simple", categorias=(1,),
    )


def test_el_nombre_igual_al_sku_con_digito_se_marca():
    r = nombre_es_codigo([ficha("INV-WIFI89283942", "INV-WIFI89283942")])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == MEDIA
    assert r.hallazgos[0].evidence["sku"] == "INV-WIFI89283942"


def test_una_palabra_como_nombre_y_sku_no_es_codigo():
    # «Paleta» es una palabra, no un código: no lleva dígito.
    r = nombre_es_codigo([ficha("Paleta", "Paleta")])
    assert r.hallazgos == []


def test_un_modelo_legitimo_no_se_marca_por_parecer_codigo():
    # CBR600RR es un modelo de moto, no el sku interno: no es «el nombre es un código».
    r = nombre_es_codigo([ficha("SKU-0001", "CBR600RR")])
    assert r.hallazgos == []


def test_el_sku_embebido_no_es_el_nombre_es_codigo():
    # El sku metido DENTRO de un nombre es el detector vecino (basura), no éste.
    r = nombre_es_codigo([ficha("INV-1234", "Paleta INV-1234 Pro")])
    assert r.hallazgos == []


def test_un_sku_corto_sin_digito_no_se_marca():
    r = nombre_es_codigo([ficha("Nox", "Nox")])
    assert r.hallazgos == []


def test_la_variante_no_navegable_no_aplica_y_la_cobertura_suma():
    fichas = [
        ficha("INV-1", "INV-1"),
        ficha("variante", "variante", visibility="1"),
    ]
    r = nombre_es_codigo(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
