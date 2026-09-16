"""La nota, y sobre todo las dos decisiones que el usuario tomó sobre ella."""

from skudo.findings.catalog import ALTA, AVISO, BAJA, CANDIDATO, MEDIA
from skudo.score.scoring import (
    HallazgoDeProducto,
    grado,
    impacto,
    nota_de_producto,
    salud_de_catalogo,
)


def h(code, severidad=MEDIA, eje=1):
    return HallazgoDeProducto(code=code, severity=severidad, axis=eje)


# --- la nota de un producto ------------------------------------------------

def test_un_producto_sin_hallazgos_es_cien_y_es_A():
    n = nota_de_producto("perfecto", [])
    assert n.puntaje == 100 and n.grado == "A" and n.critico is False


def test_los_avisos_no_bajan_la_nota():
    """Decisión del usuario: una convención editorial que el dueño eligió no es
    un defecto. Con el 97 % de los nombres en mayúsculas, penalizarlos
    condenaría al catálogo entero por un estilo."""
    n = nota_de_producto("x", [h("nombre_en_mayusculas", AVISO)])
    assert n.puntaje == 100
    assert n.deducciones == [], "el aviso no aparece como deducción"


def test_un_aviso_no_impide_ver_los_defectos_reales():
    n = nota_de_producto("x", [h("nombre_en_mayusculas", AVISO), h("sin_imagen", ALTA)])
    assert n.puntaje == 75
    assert [d["code"] for d in n.deducciones] == ["sin_imagen"]


def test_la_nota_explica_de_donde_sale():
    """Lo que Semrush explícitamente no hace: su documentación dice que la
    fórmula no se publica. Un catálogo que saca C puede preguntar por qué."""
    n = nota_de_producto("x", [h("sin_imagen", ALTA), h("sin_descripcion", MEDIA)])
    assert n.puntaje == 65 and n.grado == "C"
    assert n.deducciones == [
        {"code": "sin_descripcion", "severidad": MEDIA, "resta": 10},
        {"code": "sin_imagen", "severidad": ALTA, "resta": 25},
    ]
    assert sum(d["resta"] for d in n.deducciones) == 100 - n.puntaje


def test_una_misma_causa_no_penaliza_dos_veces():
    """Deduplicación por causa raíz: el mismo código puede llegar del detector
    por producto y del de grupo."""
    n = nota_de_producto("x", [h("sin_imagen", ALTA), h("sin_imagen", ALTA)])
    assert n.puntaje == 75


def test_la_nota_no_baja_de_cero():
    n = nota_de_producto("x", [h(f"d{i}", ALTA) for i in range(9)])
    assert n.puntaje == 0 and n.grado == "E"


def test_los_cortes_del_grado_contra_casos_concretos():
    """Los cortes se eligieron mirando productos reales, no en abstracto."""
    assert nota_de_producto("x", [h("sin_descripcion_corta", BAJA)]).grado == "A"
    assert nota_de_producto("x", [h("sin_descripcion", MEDIA)]).grado == "B"
    assert nota_de_producto("x", [h("sin_imagen", ALTA)]).grado == "C"
    assert nota_de_producto(
        "x", [h("sin_imagen", ALTA), h("sin_descripcion", MEDIA),
              h("sin_meta_title", MEDIA), h("sin_categoria", MEDIA)]
    ).grado == "D"


def test_el_grado_cubre_todo_el_rango():
    assert [grado(v) for v in (100, 95, 94, 80, 79, 60, 59, 40, 39, 0)] == [
        "A", "A", "B", "B", "C", "C", "D", "D", "E", "E"
    ]


# --- errores críticos ------------------------------------------------------

def test_sin_precio_y_sin_imagen_marcan_no_publicable():
    """Decisión del usuario: son los dos que impiden la transacción."""
    assert nota_de_producto("x", [h("sin_precio", ALTA)]).critico is True
    assert nota_de_producto("x", [h("sin_imagen", ALTA)]).critico is True


def test_una_carencia_grave_que_no_impide_vender_no_es_critica():
    assert nota_de_producto("x", [h("sin_descripcion", MEDIA)]).critico is False
    assert nota_de_producto("x", [h("variantes_por_talle", ALTA)]).critico is False


def test_un_producto_puede_tener_buena_nota_y_no_ser_publicable():
    """La razón de que los críticos pesen APARTE: presentarlos dentro del número
    escondería justo esto."""
    n = nota_de_producto("x", [h("sin_imagen", ALTA)])
    assert n.puntaje == 75 and n.grado == "C" and n.critico is True


# --- la salud del catálogo -------------------------------------------------

def test_la_salud_no_depende_del_tamano_del_catalogo():
    """De Semrush: su Site Health no depende del número de páginas porque cuenta
    la frecuencia de cada fallo. Un promedio tiene esa propiedad por
    construcción, y es lo que permite comparar un catálogo de 3.681 productos
    con uno de 228.881."""
    chico = [nota_de_producto(f"s{i}", [h("sin_imagen", ALTA)] if i < 2 else [])
             for i in range(10)]
    grande = [nota_de_producto(f"g{i}", [h("sin_imagen", ALTA)] if i < 200 else [])
              for i in range(1000)]
    assert salud_de_catalogo(chico).salud == salud_de_catalogo(grande).salud


def test_la_distribucion_muestra_lo_que_el_promedio_esconde():
    """De Akeneo. Un promedio de 93 puede esconder un 10 % de productos en E, y
    las dos cifras dicen cosas distintas."""
    notas = [nota_de_producto(f"s{i}", [] if i < 90 else [h(f"x{j}", ALTA) for j in range(4)])
             for i in range(100)]
    salud = salud_de_catalogo(notas)
    assert salud.salud == 90, "el promedio parece sano"
    assert salud.distribucion["E"] == 10, "y la distribución muestra los diez que no lo están"
    assert salud.distribucion["A"] == 90


def test_la_salud_cuenta_los_criticos_aparte():
    notas = [nota_de_producto("a", [h("sin_precio", ALTA)]),
             nota_de_producto("b", [h("sin_descripcion", MEDIA)]),
             nota_de_producto("c", [])]
    assert salud_de_catalogo(notas).criticos == 1


def test_un_catalogo_vacio_no_finge_estar_sano():
    salud = salud_de_catalogo([])
    assert salud.salud == 0 and salud.productos == 0


def test_el_subscore_por_eje_dice_donde_invertir():
    salud = salud_de_catalogo(
        [nota_de_producto("a", [h("sin_imagen", ALTA, eje=7)])],
        {"a": [h("sin_imagen", ALTA, eje=7), h("sin_descripcion", MEDIA, eje=6)]},
    )
    assert salud.por_eje == {6: 10, 7: 25}


# --- el orden del informe --------------------------------------------------

def test_el_impacto_ordena_por_dano_y_no_por_cantidad():
    """De Merchant Center. Sin esto, 1.308 avisos de mayúsculas —que además no
    penalizan— aparecerían arriba de 197 productos sin foto."""
    assert impacto(AVISO, 1308) == 0
    assert impacto(ALTA, 197) > impacto(MEDIA, 492)
    assert impacto(CANDIDATO, 100) < impacto(ALTA, 100)
