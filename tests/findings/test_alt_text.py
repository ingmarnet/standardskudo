"""Alt text ausente o igual al archivo (Eje 7).

La imagen existe pero el texto alternativo falta, o es el nombre del archivo
(lo que Magento auto-rellena al subir sin editarlo). Solo mira productos con
imagen: sin imagen ya lo cubre `sin_imagen`.
"""

from skudo.findings.catalog import BAJA, Ficha, alt_text


def ficha(sku, attributes=None, visibility="4", status="1"):
    return Ficha(
        sku=sku,
        attributes={"status": status, "visibility": visibility, **(attributes or {})},
        attribute_set_id=4,
        type_id="simple",
        categorias=(1,),
    )


def test_imagen_sin_etiqueta_se_marca_ausente():
    r = alt_text([ficha("A", {"image": "/p/a/pala-1.jpg"})])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].severity == BAJA
    assert r.hallazgos[0].evidence["tipo"] == "ausente"


def test_etiqueta_igual_al_nombre_se_marca():
    r = alt_text([ficha("A", {"image": "/p/a/pala-1.jpg", "image_label": "pala-1"})])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].evidence["tipo"] == "igual_al_archivo"


def test_etiqueta_con_extension_se_marca():
    r = alt_text([ficha("A", {"image": "/p/a/pala-1.jpg", "image_label": "pala-1.jpg"})])
    assert len(r.hallazgos) == 1


def test_etiqueta_descriptiva_no_se_marca():
    r = alt_text([ficha("A", {"image": "/p/a/pala-1.jpg", "image_label": "Pala de pádel profesional"})])
    assert r.hallazgos == []


def test_sin_imagen_no_se_marca_como_alt():
    # La ausencia de imagen es `sin_imagen`, no un problema de alt text.
    r = alt_text([ficha("A", {"image": "no_selection"})])
    assert r.hallazgos == []


def test_la_cobertura_suma():
    fichas = [
        ficha("A", {"image": "/a.jpg"}),
        ficha("B", visibility="1"),  # no navegable: no se evalúa
    ]
    r = alt_text(fichas)
    c = r.cobertura
    assert c.evaluados == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 2
