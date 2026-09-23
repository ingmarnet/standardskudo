"""Configurable sin hijos (Eje 1): un configurable sin variantes no se compra.

El sabotaje es la frontera del spec: el padre no se vende, se venden sus hijos.
Por eso el detector marca el configurable que NADIE reclama como padre —y solo
si `type_id` es configurable—, y deja intacto al configurable que tiene al menos
una variante, aunque esa variante esté deshabilitada o no navegue.
"""

from skudo.findings.catalog import MEDIA, Ficha, configurable_sin_hijos


def ficha(sku, type_id="simple", parent_skus=(), visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility},
        attribute_set_id=4,
        type_id=type_id,
        categorias=(1,),
        parent_skus=tuple(parent_skus),
    )


def test_un_configurable_sin_hijos_se_marca():
    r = configurable_sin_hijos([ficha("PADRE", type_id="configurable")])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == MEDIA
    assert r.hallazgos[0].subject_key == "PADRE"


def test_un_configurable_con_hijos_no_se_marca():
    fichas = [
        ficha("PADRE", type_id="configurable"),
        ficha("HIJO-T1", parent_skus=("PADRE",)),
    ]
    r = configurable_sin_hijos(fichas)
    assert r.hallazgos == []


def test_una_variante_simple_no_se_marca_como_configurable():
    # Una variante suelta (simple) sin padre no es un configurable sin hijos:
    # ese caso es `variantes_sueltas`, otro detector.
    r = configurable_sin_hijos([ficha("SUELTO")])
    assert r.hallazgos == []


def test_el_hijo_deshabilitado_tambien_cuenta_como_hijo():
    # Un hijo cargado pero deshabilitado sigue siendo una variante: el padre
    # no está vacío, está despublicado. No es "sin hijos".
    fichas = [
        ficha("PADRE", type_id="configurable"),
        ficha("HIJO", parent_skus=("PADRE",), status="2"),
    ]
    r = configurable_sin_hijos(fichas)
    assert r.hallazgos == []


def test_la_cobertura_suma():
    fichas = [
        ficha("PADRE-SIN-HIJOS", type_id="configurable"),
        ficha("VARIANTE", visibility="1"),  # no navegable: no se evalúa
    ]
    r = configurable_sin_hijos(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert len(r.hallazgos) == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
