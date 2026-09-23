"""Tests de comparación entre corridas: la detección de regresión.

La comparación se basa en hallazgos (Finding), no en ProductScore, porque
ProductScore se reescribe en cada pasada (una fila por producto, no por corrida).
"""

from skudo_testing import escribir, preparar, set_product_categories

from skudo.findings.run import detect_store_view
from skudo.score.run import score_run
from skudo.score.trend import comparar_ultima, ultimas_dos


def _producto_completo(session, tenant, store, sku, name):
    escribir(session, tenant, store, sku,
             {"name": name, "status": "1", "visibility": "4",
              "price": "500000", "image": "/a.jpg", "image_label": "Paleta",
              "description": "d", "short_description": "s", "meta_title": "m"})
    set_product_categories(session, tenant.id, sku, [7])


def _producto_sin_imagen(session, tenant, store, sku, name):
    escribir(session, tenant, store, sku,
             {"name": name, "status": "1", "visibility": "4",
              "price": "500000", "description": "d",
              "short_description": "s", "meta_title": "m"})
    set_product_categories(session, tenant.id, sku, [7])


def _correr(session, tenant, store=1):
    run = detect_store_view(session, tenant.id, store)
    return score_run(session, run)


def test_sin_historia_no_hay_comparacion(db_session):
    tenant = preparar(db_session)
    _producto_completo(db_session, tenant, 1, "bueno", "Paleta Nox")
    _correr(db_session, tenant)

    assert ultimas_dos(db_session, tenant.id, 1) is None
    assert comparar_ultima(db_session, tenant.id, 1) is None


def test_con_dos_corridas_iguales_no_hay_cambios(db_session):
    tenant = preparar(db_session)
    _producto_completo(db_session, tenant, 1, "bueno", "Paleta Nox")
    _producto_completo(db_session, tenant, 1, "otro", "Paleta Bull")
    _correr(db_session, tenant)
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    assert comp is not None
    assert comp.delta_salud == 0
    assert not comp.hay_regresion
    assert comp.mejoraron == []
    assert comp.empeoraron == []
    assert comp.hallazgos_nuevos == []
    assert comp.hallazgos_resueltos == []


def test_corregir_un_defecto_resuelve_hallazgos(db_session):
    """Agregar una imagen que faltaba → sin_imagen desaparece → mejora."""
    tenant = preparar(db_session)
    _producto_sin_imagen(db_session, tenant, 1, "roto", "Paleta Nox")
    _producto_completo(db_session, tenant, 1, "otro", "Paleta Bull")
    _correr(db_session, tenant)

    # Corregir: agregar la imagen.
    _producto_completo(db_session, tenant, 1, "roto", "Paleta Nox")
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    assert comp.delta_salud > 0
    assert not comp.hay_regresion
    resueltos = {h.code for h in comp.hallazgos_resueltos}
    assert "sin_imagen" in resueltos
    assert "roto" in comp.desaparecidos  # el SKU ya no tiene hallazgos


def test_empeorar_un_producto_marca_regresion(db_session):
    """Quitar la imagen → sin_imagen aparece → regresión."""
    tenant = preparar(db_session)
    _producto_completo(db_session, tenant, 1, "bueno", "Paleta Nox")
    _producto_completo(db_session, tenant, 1, "otro", "Paleta Bull")
    _correr(db_session, tenant)

    # Empeorar: quitar la imagen.
    _producto_sin_imagen(db_session, tenant, 1, "bueno", "Paleta Nox")
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    assert comp.delta_salud < 0
    assert comp.hay_regresion
    nuevos = {h.code for h in comp.hallazgos_nuevos}
    assert "sin_imagen" in nuevos
    assert "bueno" in comp.nuevos_con_hallazgos


def test_producto_nuevo_con_defecto_aparece(db_session):
    """Un producto nuevo con hallazgos aparece en nuevos_con_hallazgos."""
    tenant = preparar(db_session)
    _producto_completo(db_session, tenant, 1, "bueno", "Paleta Nox")
    _correr(db_session, tenant)

    # Agregar un producto nuevo con defecto.
    _producto_sin_imagen(db_session, tenant, 1, "nuevo-roto", "Paleta Head")
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    assert "nuevo-roto" in comp.nuevos_con_hallazgos


def test_producto_arreglado_desaparece_de_hallazgos(db_session):
    """Un producto con defectos que se corrige desaparece de la lista."""
    tenant = preparar(db_session)
    _producto_sin_imagen(db_session, tenant, 1, "roto", "Paleta Nox")
    _correr(db_session, tenant)

    _producto_completo(db_session, tenant, 1, "roto", "Paleta Nox")
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    assert "roto" in comp.desaparecidos  # ya no tiene hallazgos


def test_resumen_contiene_todas_las_claves(db_session):
    tenant = preparar(db_session)
    _producto_completo(db_session, tenant, 1, "bueno", "Paleta Nox")
    _correr(db_session, tenant)
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    r = comp.resumen()
    assert set(r.keys()) == {
        "salud_antes", "salud_ahora", "delta_salud", "regresion",
        "mejoraron", "empeoraron", "nuevos_con_hallazgos", "desaparecidos",
        "hallazgos_nuevos", "hallazgos_resueltos",
    }


def test_hallazgo_que_crece_aparece_como_cambiado(db_session):
    """Si ya había sin_imagen y ahora hay más, es un cambio, no uno nuevo."""
    tenant = preparar(db_session)
    _producto_sin_imagen(db_session, tenant, 1, "roto1", "Paleta A")
    _producto_completo(db_session, tenant, 1, "bueno", "Paleta B")
    _correr(db_session, tenant)

    _producto_sin_imagen(db_session, tenant, 1, "roto2", "Paleta C")
    _correr(db_session, tenant)

    comp = comparar_ultima(db_session, tenant.id, 1)
    cambiados = {h.code for h in comp.hallazgos_cambiados}
    assert "sin_imagen" in cambiados
    assert all(h.code != "sin_imagen" for h in comp.hallazgos_nuevos)
