"""Nombre fuera de la plantilla de su tipo (Eje 1, spec §6.2): candidato.

La plantilla es «qué atributos aparecen en los nombres bien formados». Solo un
atributo cuyo valor está metido en ≥70 % de los nombres que lo llevan pasa a ser
parte de la plantilla; un nombre que omite ese valor es un candidato, no un
veredicto. El sabotaje: un atributo que no suele ir en el nombre, un valor corto
y la falta de masa crítica no arman plantilla.
"""

from skudo.findings.catalog import CANDIDATO, Ficha, nombre_fuera_de_plantilla


def ficha(sku, attrs, set_id=4):
    base = {"name": f"p{sku}", "status": "1", "visibility": "4"}
    base.update(attrs)
    return Ficha(sku=sku, attributes=base, attribute_set_id=set_id,
                 type_id="simple", categorias=(1,))


def test_el_nombre_sin_la_marca_del_tipo_es_candidato():
    r = nombre_fuera_de_plantilla([
        ficha("a", {"marca": "Head", "name": "Paleta Head Pro"}),
        ficha("b", {"marca": "Head", "name": "Paleta Head One"}),
        ficha("c", {"marca": "Head", "name": "Paleta Head Two"}),
        ficha("d", {"marca": "Head", "name": "Paleta Pro"}),
    ])
    assert len(r.hallazgos) == 1
    h = r.hallazgos[0]
    assert h.code == "nombre_fuera_de_plantilla"
    assert h.axis == 1
    assert h.severity == CANDIDATO
    assert h.subject_key == "d"
    assert h.evidence["faltan"] == ["marca"]


def test_un_atributo_que_no_suele_ir_en_el_nombre_no_es_plantilla():
    # El color se guarda aparte y nadie lo mete en el nombre: no es plantilla.
    r = nombre_fuera_de_plantilla([
        ficha("a", {"color": "Rojo", "name": "Paleta Alpha"}),
        ficha("b", {"color": "Azul", "name": "Paleta Beta"}),
        ficha("c", {"color": "Verde", "name": "Paleta Gamma"}),
        ficha("d", {"color": "Rojo", "name": "Paleta Delta"}),
    ])
    assert r.hallazgos == []


def test_un_valor_corto_no_es_parte_de_la_plantilla():
    # «si» es un código, no una palabra de nombre: excluirlo evita que coincida
    # por casualidad con una sílaba ("simple", "sistemática").
    r = nombre_fuera_de_plantilla([
        ficha("a", {"fragil": "si", "name": "Paleta Simple"}),
        ficha("b", {"fragil": "si", "name": "Paleta Sistematica"}),
        ficha("c", {"fragil": "si", "name": "Paleta Silenciosa"}),
        ficha("d", {"fragil": "si", "name": "Paleta Pro"}),
    ])
    assert r.hallazgos == []


def test_sin_masa_critica_no_hay_plantilla():
    r = nombre_fuera_de_plantilla([
        ficha("a", {"marca": "Head", "name": "Paleta Head Pro"}),
        ficha("b", {"marca": "Head", "name": "Paleta Pro"}),
    ])
    assert r.hallazgos == []


def test_el_nombre_que_omite_dos_atributos_los_lista():
    r = nombre_fuera_de_plantilla([
        ficha("a", {"marca": "Head", "modelo": "ML10", "name": "Paleta Head ML10"}),
        ficha("b", {"marca": "Head", "modelo": "ML10", "name": "Paleta Head ML10"}),
        ficha("c", {"marca": "Head", "modelo": "ML10", "name": "Paleta Head ML10"}),
        ficha("d", {"marca": "Head", "modelo": "ML10", "name": "Paleta"}),
    ])
    assert len(r.hallazgos) == 1
    assert r.hallazgos[0].evidence["faltan"] == ["marca", "modelo"]


def test_la_cobertura_suma_el_universo():
    fichas = [
        ficha("a", {"marca": "Head", "name": "Paleta Head Pro"}),
        ficha("b", {"marca": "Head", "name": "Paleta Head One"}),
        ficha("c", {"marca": "Head", "name": "Paleta Head Two"}),
        ficha("d", {"marca": "Head", "name": "Paleta Pro"}),
        Ficha(sku="v", attributes={"name": "v", "status": "1", "visibility": "1"},
              attribute_set_id=4, type_id="simple", categorias=(1,)),
    ]
    r = nombre_fuera_de_plantilla(fichas)
    c = r.cobertura
    assert c.universo == 5
    assert c.evaluados == 4  # los publicados; la variante no navegable no aplica
    assert c.evaluados + c.no_aplica + c.no_evaluado == 5
