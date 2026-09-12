import pytest

from skudo.profile.coverage import Counts
from skudo.profile.partition import ambiguity, gain, weighted_ambiguity


def vector(**coberturas):
    """Un vector con la cobertura pedida: `color=0.5` son 1 presente y 1 vacío."""
    salida = {}
    for code, c in coberturas.items():
        if c is None:
            salida[code] = Counts(desconocido=10)
        else:
            salida[code] = Counts(presente=int(c * 100), vacio=int((1 - c) * 100))
    return salida


def test_un_atributo_que_tienen_todos_no_es_ambiguo():
    assert ambiguity(vector(color=1.0)) == 0.0


def test_un_atributo_que_no_tiene_nadie_tampoco_es_ambiguo():
    """Cero por ciento es una afirmación tan útil como cien: dice que ese
    atributo no pertenece a la ficha de este grupo."""
    assert ambiguity(vector(color=0.0)) == 0.0


def test_la_mitad_es_el_maximo_de_ambiguedad():
    assert ambiguity(vector(color=0.5)) == pytest.approx(0.5)
    assert ambiguity(vector(color=0.5)) > ambiguity(vector(color=0.9))


def test_los_atributos_no_medibles_no_entran_en_la_media():
    assert ambiguity(vector(color=0.5, peso=None)) == pytest.approx(0.5)


def test_un_vector_sin_nada_medible_no_tiene_ambiguedad_definida():
    assert ambiguity(vector(color=None)) is None
    assert ambiguity({}) is None


def test_la_ambiguedad_de_una_division_pondera_por_tamano():
    assert weighted_ambiguity([(90, 0.0), (10, 0.5)]) == pytest.approx(0.05)


def test_una_division_con_un_grupo_no_medible_no_es_evaluable():
    """Conservador a propósito: si un grupo no se puede medir, la ganancia
    aparente del resto es una ilusión, y una ilusión que siempre favorece
    dividir."""
    assert weighted_ambiguity([(90, 0.1), (10, None)]) is None


def test_la_ganancia_es_lo_que_baja_la_ambiguedad():
    assert gain(0.5, 0.05) == pytest.approx(0.45)
    assert gain(0.5, None) is None
    assert gain(None, 0.1) is None
