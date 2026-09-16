"""Los detectores, y sobre todo sus EXCLUSIONES.

Cada exclusión de este archivo nació de un falso positivo medido contra el
catálogo real de Renovapadel el 2026-09-16. Los tests que las guardan valen
más que los que comprueban el camino feliz: el camino feliz lo acierta
cualquiera.
"""

import pytest

from skudo.findings.catalog import (
    ALTA,
    AVISO,
    CANDIDATO,
    DESCONOCIDO,
    PUBLICADO,
    Ficha,
    evaluar,
    nombre_en_mayusculas,
    nombres_repetidos,
    normalizar_nombre,
    sin_categoria,
    sin_imagen,
    sin_precio,
)


def ficha(sku, *, nombre="Paleta Bullpadel", status="1", visibility="4", tipo="simple",
          cats=(1,), **attrs):
    base = {"name": nombre, "status": status, "visibility": visibility}
    base.update(attrs)
    return Ficha(sku=sku, attributes=base, type_id=tipo, categorias=tuple(cats))


# --- estado ---------------------------------------------------------------

def test_los_tres_estados_de_vitrina():
    assert ficha("a").estado == PUBLICADO
    assert ficha("b", visibility="1").estado == "no_navegable"
    assert ficha("c", status="2").estado == "deshabilitado"


def test_sin_status_o_visibility_el_estado_es_desconocido():
    """No se supone que esté publicado: se declara que no se pudo mirar."""
    assert Ficha("x", {"name": "N", "visibility": "4"}).estado == DESCONOCIDO
    assert Ficha("x", {"name": "N", "status": "1"}).estado == DESCONOCIDO


# --- sin_imagen -----------------------------------------------------------

def test_sin_imagen_solo_cuenta_publicados():
    """El falso positivo original: 1.895 pasaron a 197 al excluir variantes."""
    fichas = [
        ficha("publicado-sin-foto"),
        ficha("variante-sin-foto", visibility="1"),
        ficha("apagado-sin-foto", status="2"),
        ficha("publicado-con-foto", image="/p/a.jpg"),
    ]
    r = sin_imagen(fichas)
    assert [h.subject_key for h in r.hallazgos] == ["publicado-sin-foto"]
    assert r.cobertura.evaluados == 2
    assert r.cobertura.no_aplica == 2
    assert r.cobertura.motivo_no_aplica


def test_no_selection_es_ausencia_de_imagen():
    """Centinela de Magento: cadena no vacía que significa vacío. Un `if valor:`
    la deja pasar y el producto queda sin marcar."""
    assert sin_imagen([ficha("x", image="no_selection")]).hallazgos


def test_lo_desconocido_no_se_evalua_ni_se_aprueba():
    r = sin_imagen([Ficha("x", {"name": "N"})])
    assert r.hallazgos == []
    assert r.cobertura.no_evaluado == 1
    assert r.cobertura.evaluados == 0


# --- sin_precio -----------------------------------------------------------

def test_un_configurable_sin_precio_no_es_un_defecto():
    """374 de 375 «sin precio» eran configurables. El precio lo heredan de sus
    hijos: exigírselo inventa un defecto en el 28 % del catálogo."""
    r = sin_precio([
        ficha("conf", tipo="configurable"),
        ficha("agrupado", tipo="grouped"),
        ficha("simple-sin-precio"),
        ficha("simple-con-precio", price="150000"),
    ])
    assert [h.subject_key for h in r.hallazgos] == ["simple-sin-precio"]
    assert r.cobertura.no_aplica == 2
    assert "configurables" in r.cobertura.motivo_no_aplica


def test_precio_cero_es_un_hallazgo_pero_stock_cero_no_lo_seria():
    """En precio, el cero SÍ es un defecto —un producto no vale nada— y por eso
    se mira el valor además de la presencia. Es la excepción deliberada a la
    regla de 'presencia, no verdad'."""
    assert sin_precio([ficha("x", price="0")]).hallazgos
    assert not sin_precio([ficha("x", price="0.01")]).hallazgos


# --- nombres repetidos ----------------------------------------------------

def test_ocho_talles_con_el_mismo_nombre_son_UN_hallazgo():
    """La deduplicación por causa raíz: ocho hallazgos serían la misma causa
    contada ocho veces."""
    fichas = [ficha(f"166{i}", nombre="CALZADO ASICS GEL CHALLENGER 14") for i in range(8)]
    r = nombres_repetidos(fichas)
    assert len(r.hallazgos) == 1
    h = r.hallazgos[0]
    assert h.code == "variantes_sueltas"
    assert h.severity == ALTA
    assert h.evidence["productos"] == 8
    assert len(h.evidence["skus"]) <= 10


def test_dos_productos_con_el_mismo_nombre_son_CANDIDATO_no_veredicto():
    """Dos registros con nombre idéntico pueden ser dos productos distintos."""
    r = nombres_repetidos([ficha("a", nombre="Grip Negro"), ficha("b", nombre="Grip Negro")])
    assert r.hallazgos[0].code == "nombre_repetido"
    assert r.hallazgos[0].severity == CANDIDATO


def test_un_grupo_que_ya_tiene_configurable_no_se_lee_como_variante_suelta():
    fichas = [ficha(f"s{i}", nombre="Zapatilla X") for i in range(3)]
    fichas.append(ficha("padre", nombre="Zapatilla X", tipo="configurable"))
    r = nombres_repetidos(fichas)
    assert r.hallazgos[0].code == "nombre_repetido"


def test_nombres_distintos_no_generan_hallazgo():
    r = nombres_repetidos([ficha("a", nombre="Uno"), ficha("b", nombre="Dos")])
    assert r.hallazgos == []


def test_la_comparacion_de_nombres_ignora_tildes_mayusculas_y_espacios():
    assert normalizar_nombre("CALZADO  ASICS") == normalizar_nombre("Calzado Asics")
    assert normalizar_nombre("Paletá") == normalizar_nombre("paleta")


# --- resto ----------------------------------------------------------------

def test_sin_categoria():
    r = sin_categoria([ficha("huerfano", cats=()), ficha("ubicado", cats=(3, 7))])
    assert [h.subject_key for h in r.hallazgos] == ["huerfano"]


def test_mayusculas_es_aviso_y_no_defecto():
    """97 % del catálogo real las usa: es la convención de carga, y corregir la
    convención de alguien sin preguntarle no es trabajo de esta herramienta."""
    r = nombre_en_mayusculas([ficha("x", nombre="CALZADO ASICS GEL")])
    assert r.hallazgos[0].severity == AVISO


def test_las_siglas_cortas_no_se_marcan():
    assert not nombre_en_mayusculas([ficha("x", nombre="NOX AT10")]).hallazgos


def test_un_nombre_sin_mayusculas_posibles_no_se_marca():
    """Un nombre de solo dígitos y símbolos es igual en mayúsculas: marcarlo
    sería marcar a todos los que no tienen letras."""
    assert not nombre_en_mayusculas([ficha("x", nombre="12345678901")]).hallazgos


# --- el conjunto ----------------------------------------------------------

def test_todos_los_detectores_declaran_su_cobertura():
    """La propiedad que hace confiable al conjunto: ninguno puede devolver
    hallazgos sin decir sobre cuántos productos los buscó."""
    fichas = [ficha("a"), ficha("b", visibility="1"), Ficha("c", {"name": "N"})]
    for r in evaluar(fichas):
        assert r.cobertura.detector
        assert r.cobertura.universo == 3, r.cobertura.detector
        assert r.cobertura.no_evaluado == 1, r.cobertura.detector


def test_ningun_detector_marca_un_producto_desconocido():
    fichas = [Ficha(f"x{i}", {"name": "N"}) for i in range(5)]
    for r in evaluar(fichas):
        assert r.hallazgos == [], r.cobertura.detector


@pytest.mark.parametrize("severidad", [ALTA, AVISO, CANDIDATO])
def test_el_vocabulario_de_severidad_es_cerrado(severidad):
    from skudo.findings.catalog import SEVERIDADES
    assert severidad in SEVERIDADES
