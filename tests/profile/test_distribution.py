import pytest

from skudo.profile.distribution import Lectura, parse_number, percentil, value_stats


def test_lee_un_entero_y_un_decimal_con_punto():
    assert parse_number("12") == Lectura(12.0, "leido")
    assert parse_number("2.5") == Lectura(2.5, "leido")


def test_lee_la_coma_decimal():
    """PY y BR escriben 2,5. Rechazarlo tiraría media distribución."""
    assert parse_number("2,5") == Lectura(2.5, "leido")


def test_con_los_dos_separadores_el_ultimo_manda():
    """Funciona para las dos convenciones sin adivinar cuál se usó."""
    assert parse_number("1.234,56") == Lectura(1234.56, "leido")
    assert parse_number("1,234.56") == Lectura(1234.56, "leido")


def test_un_separador_con_tres_decimales_es_ambiguo_y_no_se_adivina():
    """`1,250` es 1250 o 1,25 según quién lo escribió, y la diferencia es de
    mil veces: exactamente la sospecha de conversión que el spec quiere
    detectar, no propagar. Se cuenta aparte y no entra en la distribución."""
    assert parse_number("1,250") == Lectura(None, "ambiguo")
    assert parse_number("1.250") == Lectura(None, "ambiguo")


def test_lo_que_no_es_un_numero_se_dice_asi():
    assert parse_number("2.5 kg") == Lectura(None, "no_numerico")
    assert parse_number("") == Lectura(None, "no_numerico")
    assert parse_number("N/A") == Lectura(None, "no_numerico")


def test_el_cero_se_lee_como_cero():
    assert parse_number("0") == Lectura(0.0, "leido")


def test_percentil_por_rango_mas_cercano():
    valores = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentil(valores, 50) == 3.0
    assert percentil(valores, 5) == 1.0
    assert percentil(valores, 95) == 5.0
    assert percentil([7.0], 50) == 7.0


def test_un_atributo_numerico_trae_su_distribucion():
    stats = value_stats([str(v) for v in range(1, 101)], "text")
    assert stats.kind == "numerico"
    assert stats.n_present == 100
    assert stats.minimum == 1.0
    assert stats.maximum == 100.0
    assert stats.p50 == pytest.approx(50.0)


def test_los_ambiguos_se_cuentan_y_no_contaminan():
    stats = value_stats(["1", "2", "3", "1,250"], "text")
    assert stats.n_ambiguous == 1
    assert stats.maximum == 3.0


def test_un_atributo_de_opcion_trae_cardinalidad_y_poder():
    stats = value_stats(["rojo"] * 9 + ["azul"], "select")
    assert stats.kind == "opcion"
    assert stats.distinct_values == 2
    assert stats.mode_share == pytest.approx(0.9)
    assert stats.discriminating_power == pytest.approx(0.1)


def test_un_atributo_que_vale_lo_mismo_para_todos_no_discrimina():
    """Es la base de la plantilla de nombre: meter en el nombre un atributo que
    todos comparten no distingue nada."""
    stats = value_stats(["negro"] * 50, "select")
    assert stats.discriminating_power == 0.0


def test_los_valores_mas_frecuentes_van_ordenados_y_acotados():
    stats = value_stats(["a"] * 5 + ["b"] * 3 + ["c"] * 2 + [f"x{i}" for i in range(30)], "select")
    assert [v for v, _ in stats.top_values] == ["a", "b", "c"] + sorted(
        f"x{i}" for i in range(30)
    )[: 10 - 3]
    assert len(stats.top_values) == 10


def test_sin_valores_presentes_todo_es_nulo_y_nada_es_cero():
    stats = value_stats([], "select")
    assert stats.n_present == 0
    assert stats.distinct_values is None
    assert stats.discriminating_power is None
    assert stats.minimum is None
