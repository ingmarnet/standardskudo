"""Marca inconsistente (Eje 1): la misma marca escrita de más de una forma.

Candidato, un hallazgo por grafía minoritaria (no por producto). El sabotaje es
la dirección del hallazgo: solo la grafía MENOS usada se marca, nunca la
canónica; y una grafía con la misma frecuencia es ambigua, no se marca.
"""

from skudo.findings.catalog import CANDIDATO, Ficha, marca_inconsistente


def ficha(sku, manufacturer, visibility="4", set_id=4):
    return Ficha(
        sku=sku,
        attributes={"status": "1", "visibility": visibility,
                    "manufacturer": manufacturer},
        attribute_set_id=set_id, type_id="simple", categorias=(1,),
    )


def test_una_grafia_minoritaria_se_marca_apuntando_a_la_dominante():
    fichas = [ficha(f"s{i}", "Samsung") for i in range(9)]
    fichas.append(ficha("err", "Sansung"))
    r = marca_inconsistente(fichas)
    assert len(r.hallazgos) == 1
    h = r.hallazgos[0]
    assert h.severity == CANDIDATO
    assert h.evidence["marca"] == "Sansung"
    assert h.evidence["candidata_a"] == "Samsung"
    assert h.evidence["skus"] == ["err"]


def test_la_misma_grafia_con_distinta_caja_no_es_inconsistente():
    # «SAMSUNG» y «Samsung» normalizan a lo mismo: no es inconsistencia.
    r = marca_inconsistente([ficha("a", "SAMSUNG"), ficha("b", "Samsung")])
    assert r.hallazgos == []


def test_dos_marcas_distintas_no_se_marcan():
    r = marca_inconsistente([
        ficha("a", "Nox"), ficha("b", "Bullpadel"), ficha("c", "Head"),
    ])
    assert r.hallazgos == []


def test_frecuencias_empatadas_no_se_marcan():
    # Sin grafía dominante no hay a qué apuntar: ambiguo, no se adivina.
    r = marca_inconsistente([ficha("a", "Samsung"), ficha("b", "Sansung")])
    assert r.hallazgos == []


def test_sin_atributo_de_marca_no_hay_hallazgo_pero_hay_cobertura():
    r = marca_inconsistente([ficha("a", "")])
    assert r.hallazgos == []
    assert r.cobertura.evaluados == 1


def test_la_variante_no_navegable_no_aplica_y_la_cobertura_suma():
    fichas = [
        ficha("a", "Samsung"), ficha("b", "Samsung"), ficha("c", "Sansung"),
        ficha("v", "Samsung", visibility="1"),
    ]
    r = marca_inconsistente(fichas)
    c = r.cobertura
    assert c.evaluados == 3
    assert len(r.hallazgos) == 1
    assert c.evaluados + c.no_aplica + c.no_evaluado == 4
