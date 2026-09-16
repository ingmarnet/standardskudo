"""El informe generado. Dos cosas se vigilan: que no invente y que no rompa."""


import pytest
from skudo_testing import escribir, preparar

from skudo.findings.catalog import DETECTORES
from skudo.findings.run import detect_store_view
from skudo.report.html import mil, render
from skudo.report.textos import TEXTOS


def test_todo_detector_tiene_su_texto_en_castellano():
    """Un hallazgo sin texto no se pierde —sale con su código crudo— pero este
    test falla, que es cómo nos enteramos de que falta escribirlo."""
    codigos = set()
    for d in DETECTORES:
        codigos.add(d.__name__)
    # `nombres_repetidos` emite dos códigos distintos; los demás emiten el suyo.
    emitidos = (codigos - {"nombres_repetidos"}) | {"nombre_repetido", "variantes_sueltas"}
    faltan = emitidos - set(TEXTOS)
    assert not faltan, f"detectores sin texto para el informe: {sorted(faltan)}"


def test_el_separador_de_miles_no_toca_nada_mas():
    assert mil(3681) == "3.681"
    assert mil(197) == "197"
    assert mil(1352) == "1.352"


@pytest.fixture
def informe(db_session):
    tenant = preparar(db_session)
    escribir(db_session, tenant, 1, "pub-sin-foto",
             {"name": "Paleta Nox AT10", "status": "1", "visibility": "4"})
    escribir(db_session, tenant, 1, "variante",
             {"name": "Paleta Nox AT10 T2", "status": "1", "visibility": "1"})
    run = detect_store_view(db_session, tenant.id, 1)
    return render(db_session, run, tienda="tienda.test")


def test_el_informe_dice_sobre_cuantos_productos_midio(informe):
    """La propiedad que lo hace auditable: el número nunca viaja solo."""
    assert "Medido sobre" in informe
    assert "No se evaluaron 1" in informe


def test_el_informe_no_confunde_registros_con_publicados(informe):
    assert "Por qué el denominador no es 2" in informe


def test_un_nombre_con_html_no_rompe_la_pagina(db_session):
    """Los nombres vienen del catálogo de un cliente y salen a un archivo que
    alguien abre en un navegador. Se escapan todos, sin confiar en ninguno."""
    tenant = preparar(db_session)
    escribir(db_session, tenant, 1, "<script>alert(1)</script>",
             {"name": "Paleta <b>Nox</b> & Cía", "status": "1", "visibility": "4"})
    salida = render(db_session, detect_store_view(db_session, tenant.id, 1),
                    tienda="tienda.test")

    assert "<script>alert(1)</script>" not in salida
    assert "&lt;script&gt;" in salida


def test_el_informe_es_html_cerrado(informe):
    assert informe.startswith("<!doctype html>")
    assert informe.rstrip().endswith("</html>")
    assert informe.count("<div") == informe.count("</div>")
