"""Dedup por causa raíz: un detector y una regla sobre el MISMO atributo son
una sola causa (spec §6.4: sin doble penalización)."""

from skudo.findings.catalog import MEDIA
from skudo.score.scoring import HallazgoDeProducto, nota_de_producto


def test_detector_y_regla_sobre_el_mismo_atributo_descuentan_una_vez():
    # sin_descripcion (detector) y regla:obligatoriedad:description (regla) son
    # la MISMA causa: el atributo description vacío. Debe pesar una vez.
    hallazgos = [
        HallazgoDeProducto(code="sin_descripcion", severity=MEDIA, axis=6,
                           causa="description"),
        HallazgoDeProducto(code="regla:obligatoriedad:description", severity=MEDIA,
                           axis=6, causa="description"),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 1
    assert nota.puntaje == 90  # 100 - 10, una sola vez


def test_dos_atributos_distintos_descuentan_dos_veces():
    hallazgos = [
        HallazgoDeProducto(code="regla:obligatoriedad:color", severity=MEDIA, axis=3,
                           causa="color"),
        HallazgoDeProducto(code="regla:obligatoriedad:marca", severity=MEDIA, axis=3,
                           causa="marca"),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 2


def test_sin_causa_deduplica_por_code_como_antes():
    hallazgos = [
        HallazgoDeProducto(code="sin_imagen", severity="alta", axis=7),
        HallazgoDeProducto(code="sin_imagen", severity="alta", axis=7),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 1
