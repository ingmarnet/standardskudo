"""Sospecha de conversión de unidad (Eje 4): candidato, nunca corrección.

La prueba de sabotaje es el corazón: un factor que NO es limpio no se marca, el
precio —que cambia de orden de magnitud sin cambiar de unidad— se excluye, y un
valor en cero no divide por cero.
"""

from skudo.findings.catalog import CANDIDATO, Ficha, sospecha_conversion


def ficha(sku, attrs, set_id=4):
    base = {"name": f"p{sku}", "status": "1", "visibility": "4"}
    base.update(attrs)
    return Ficha(sku=sku, attributes=base, attribute_set_id=set_id,
                 type_id="simple", categorias=(1,))


def test_un_valor_x1000_fuera_de_la_mediana_es_candidato():
    r = sospecha_conversion([
        ficha("a", {"peso": "20"}), ficha("b", {"peso": "20"}),
        ficha("c", {"peso": "20"}), ficha("d", {"peso": "20000"}),
    ])
    assert len(r.hallazgos) == 1
    h = r.hallazgos[0]
    assert h.code == "sospecha_conversion"
    assert h.axis == 4
    assert h.severity == CANDIDATO
    assert h.subject_key == "d"
    assert h.evidence["attribute"] == "peso"
    assert h.evidence["factor"] == 1000.0


def test_un_submultiplo_tambien_cuenta():
    # 20 contra una mediana de 2000 es /100: gramos cargados donde van kilos.
    r = sospecha_conversion([
        ficha("a", {"peso": "2000"}), ficha("b", {"peso": "2000"}),
        ficha("c", {"peso": "2000"}), ficha("d", {"peso": "20"}),
    ])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].subject_key == "d"
    assert r.hallazgos[0].evidence["factor"] == 100.0


def test_un_factor_no_limpio_no_se_marca():
    # ×2 es otro producto, no un dato mal: una conversión de unidad es ×10.
    r = sospecha_conversion([
        ficha("a", {"peso": "20"}), ficha("b", {"peso": "20"}),
        ficha("c", {"peso": "20"}), ficha("d", {"peso": "40"}),
    ])
    assert r.hallazgos == []


def test_el_precio_no_es_una_magnitud():
    # 50/500/5000/50000 son factores limpios ×10, pero en PRECIO son categorías
    # de producto distintas (Eje 7), no una conversión de unidad (Eje 4).
    r = sospecha_conversion([
        ficha("a", {"price": "50"}), ficha("b", {"price": "500"}),
        ficha("c", {"price": "5000"}), ficha("d", {"price": "50000"}),
    ])
    assert r.hallazgos == []


def test_sin_masa_critica_no_hay_mediana():
    r = sospecha_conversion([
        ficha("a", {"peso": "20"}), ficha("b", {"peso": "20000"}),
    ])
    assert r.hallazgos == []


def test_un_valor_en_cero_no_divide_por_cero():
    # «0» es un valor presente (no una ausencia): no debe reventar el detector
    # al dividir contra él, ni disparar una conversión.
    r = sospecha_conversion([
        ficha("a", {"peso": "20"}), ficha("b", {"peso": "20"}),
        ficha("c", {"peso": "20"}), ficha("d", {"peso": "0"}),
    ])
    assert r.hallazgos == []


def test_la_cobertura_suma_el_universo():
    fichas = [
        ficha("a", {"peso": "20"}), ficha("b", {"peso": "20"}),
        ficha("c", {"peso": "20"}), ficha("d", {"peso": "20000"}),
        Ficha(sku="v", attributes={"name": "v", "status": "1", "visibility": "1"},
              attribute_set_id=4, type_id="simple", categorias=(1,)),
    ]
    r = sospecha_conversion(fichas)
    c = r.cobertura
    assert c.universo == 5
    assert c.evaluados == 4  # los publicados; la variante no navegable no aplica
    assert c.evaluados + c.no_aplica + c.no_evaluado == 5
