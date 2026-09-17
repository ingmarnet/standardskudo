import pytest

from skudo.rules.transitions import (
    ORIGENES,
    TransicionInvalida,
    exigir_transicion,
    puede_transicionar,
)


def test_una_regla_inferida_se_acepta_desde_borrador():
    assert puede_transicionar("inferida", "borrador", "aceptada")


def test_el_piso_externo_no_se_puede_rechazar_desde_ningun_estado():
    # Éste es el candado del spec §5. Se recorre TODO origen y estado.
    for from_status in ("aceptada", "aviso", "borrador"):
        assert not puede_transicionar("piso_externo", from_status, "rechazada")
    # y sí se puede acotar a aviso
    assert puede_transicionar("piso_externo", "aceptada", "aviso")


def test_solo_el_piso_prohibe_el_rechazo():
    """La prohibición es exclusiva del piso: inferida y curada sí rechazan."""
    for origin in ("inferida", "curada"):
        assert puede_transicionar(origin, "aceptada", "rechazada")


def test_exigir_transicion_invalida_levanta():
    with pytest.raises(TransicionInvalida):
        exigir_transicion("piso_externo", "aceptada", "rechazada")


def test_exigir_transicion_valida_no_levanta():
    exigir_transicion("inferida", "borrador", "aceptada")  # no raise


def test_una_transicion_a_un_estado_desconocido_es_invalida():
    assert not puede_transicionar("inferida", "borrador", "publicada")


def test_todos_los_origenes_estan_cubiertos():
    # Si mañana se agrega un origen, este test obliga a decidir sus transiciones.
    assert ORIGENES == ("inferida", "piso_externo", "curada")
