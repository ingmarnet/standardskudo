from skudo.profile.states import State, attribute_state

SETS = {"color": frozenset({4, 9}), "peso": frozenset({4}), "voltaje": frozenset({9})}


def estado(attributes, set_id, code):
    return attribute_state(attributes, set_id, code, SETS)


def test_un_valor_normal_es_presente():
    assert estado({"color": "negro"}, 4, "color") is State.PRESENTE


def test_el_cero_es_un_valor_presente():
    """La trampa que más datos legítimos destruye: evaluar la verdad de la
    cadena en vez de su presencia. Un peso declarado como 0 es una afirmación."""
    assert estado({"peso": "0"}, 4, "peso") is State.PRESENTE
    assert estado({"peso": "0.0"}, 4, "peso") is State.PRESENTE
    assert estado({"peso": 0}, 4, "peso") is State.PRESENTE


def test_la_clave_ausente_en_un_atributo_del_set_es_vacio():
    """El módulo devuelve lo que existe en las tablas EAV. Un atributo del set
    sin fila es la carencia que el eje 3 existe para encontrar; leerlo como
    'no aplica' apagaría el eje entero."""
    assert estado({}, 4, "color") is State.VACIO


def test_la_cadena_vacia_y_los_espacios_son_vacio():
    assert estado({"color": ""}, 4, "color") is State.VACIO
    assert estado({"color": "   "}, 4, "color") is State.VACIO
    assert estado({"color": []}, 4, "color") is State.VACIO


def test_un_atributo_fuera_del_set_del_producto_no_aplica():
    assert estado({}, 4, "voltaje") is State.NO_APLICA


def test_sin_attribute_set_conocido_todo_es_desconocido():
    """`attribute_set_id` NULL significa que la sonda no lo informó. Sin él no
    se puede distinguir vacío de no_aplica, y fingir cualquiera de los dos es
    inventar un hecho."""
    assert estado({"color": "negro"}, None, "color") is State.DESCONOCIDO
    assert estado({}, None, "color") is State.DESCONOCIDO


def test_un_atributo_que_el_espejo_no_conoce_es_desconocido():
    assert estado({"raro": "x"}, 4, "raro") is State.DESCONOCIDO


def test_los_cuatro_estados_son_cuatro_valores_distintos():
    assert len({s.value for s in State}) == 4


from skudo.mirror.attributes import upsert_attribute
from skudo.mirror.models import Tenant
from skudo.profile.states import codes_by_set, sets_by_code


def test_los_indices_salen_del_espejo_y_van_ordenados(db_session):
    tenant = Tenant(code="acme", name="A", base_url="http://a.test", token_env_var="X")
    db_session.add(tenant)
    db_session.flush()
    for code, ids in [("peso", [4]), ("color", [9, 4]), ("voltaje", [9])]:
        upsert_attribute(
            db_session,
            tenant.id,
            {
                "code": code,
                "label": code,
                "frontend_input": "text",
                "declared_scope": "global",
                "is_filterable": False,
                "is_required": False,
                "attribute_set_ids": ids,
            },
            sync_generation=1,
        )

    assert sets_by_code(db_session, tenant.id)["color"] == frozenset({4, 9})
    assert codes_by_set(db_session, tenant.id) == {
        4: ("color", "peso"),
        9: ("color", "voltaje"),
    }
