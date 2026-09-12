import pytest

from skudo.profile.coverage import Counts, ProductoPerfilado, coverage_vector

SETS = {"color": frozenset({4}), "peso": frozenset({4}), "voltaje": frozenset({9})}
CODES = ("color", "peso")


def producto(attrs, set_id=4):
    return ProductoPerfilado(
        sku=f"sku{id(attrs)}", attributes=attrs, attribute_set_id=set_id, categorias=()
    )


def test_cuenta_presentes_y_vacios():
    vector = coverage_vector(
        [producto({"color": "negro"}), producto({}), producto({"color": "rojo"})],
        CODES,
        SETS,
    )
    assert vector["color"] == Counts(presente=2, vacio=1)
    assert vector["color"].cobertura == pytest.approx(2 / 3)


def test_sin_denominador_la_cobertura_es_none_y_no_cero():
    vector = coverage_vector([producto({"color": "negro"}, set_id=None)], CODES, SETS)
    assert vector["color"] == Counts(desconocido=1)
    assert vector["color"].cobertura is None


def test_cero_por_ciento_y_no_medible_no_son_lo_mismo():
    nadie = coverage_vector([producto({}), producto({})], CODES, SETS)["color"]
    nada = coverage_vector([producto({}, set_id=None)], CODES, SETS)["color"]
    assert nadie.cobertura == 0.0
    assert nada.cobertura is None


def test_dentro_de_una_particion_nadie_cae_en_no_aplica():
    """Invariante de construcción: una partición agrupa productos del MISMO set,
    y el vector solo recorre los atributos de ese set. Si aparece un `no_aplica`
    es que alguien mezcló sets en una partición."""
    vector = coverage_vector([producto({"color": "x"}), producto({})], CODES, SETS)
    assert all(c.no_aplica == 0 for c in vector.values())


def test_el_vector_solo_tiene_los_atributos_pedidos():
    vector = coverage_vector([producto({"voltaje": "220"})], CODES, SETS)
    assert sorted(vector) == ["color", "peso"]


def test_el_vector_esta_ordenado():
    vector = coverage_vector([producto({})], ("peso", "color"), SETS)
    assert list(vector) == ["color", "peso"]
