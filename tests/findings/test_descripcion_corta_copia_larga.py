"""Descripción corta copiada de la larga (Eje 6).

La corta debe resumir; si es idéntica a la larga no aporta nada y duplica
contenido. Igualdad literal: un resumen no es la larga con dos comas menos.
"""

from skudo.findings.catalog import BAJA, Ficha, descripcion_corta_copia_larga


def ficha(sku, attributes=None, visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility, **(attributes or {})},
        attribute_set_id=4,
        type_id="simple",
        categorias=(1,),
    )


def test_una_corta_identica_a_la_larga_se_marca():
    r = descripcion_corta_copia_larga([
        ficha("A", {"description": "Pala de pádel profesional", "short_description": "Pala de pádel profesional"}),
    ])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == BAJA
    assert r.hallazgos[0].subject_key == "A"


def test_una_corta_que_resume_no_se_marca():
    r = descripcion_corta_copia_larga([
        ficha("A", {"description": "Pala de pádel profesional para torneo", "short_description": "Pala de torneo"}),
    ])
    assert r.hallazgos == []


def test_una_corta_vacia_no_se_marca_como_copia():
    # La ausencia es `sin_descripcion_corta`, no una copia.
    r = descripcion_corta_copia_larga([
        ficha("A", {"description": "Pala de pádel profesional"}),
    ])
    assert r.hallazgos == []


def test_una_larga_vacia_no_se_marca_como_copia():
    r = descripcion_corta_copia_larga([
        ficha("A", {"short_description": "Pala de pádel"}),
    ])
    assert r.hallazgos == []


def test_la_cobertura_suma():
    fichas = [
        ficha("A", {"description": "X", "short_description": "X"}),
        ficha("B", visibility="1"),  # no navegable: no se evalúa
    ]
    r = descripcion_corta_copia_larga(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert len(r.hallazgos) == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
