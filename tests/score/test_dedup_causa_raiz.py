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


def test_el_critico_sobrevive_aunque_el_dedup_haga_ganar_a_la_regla():
    # sin_imagen (ALTA, código crítico) y una regla de la MISMA causa, también
    # ALTA: en empate de peso el dedup conserva el PRIMERO que llegó — acá la
    # regla, que no es código crítico. El flag `critico` no debe depender de
    # cuál de los dos gana el dedup: se calcula sobre los hallazgos crudos.
    hallazgos = [
        HallazgoDeProducto(code="regla:obligatoriedad:image", severity="alta",
                           axis=7, causa="image"),
        HallazgoDeProducto(code="sin_imagen", severity="alta", axis=7, causa="image"),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 1  # una sola causa, un solo descuento
    assert nota.critico is True  # pero sigue no-publicable


def test_sin_precio_solo_marca_critico():
    nota = nota_de_producto(
        "P1", [HallazgoDeProducto(code="sin_precio", severity="alta", axis=1,
                                  causa="price")]
    )
    assert nota.critico is True
