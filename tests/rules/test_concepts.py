from skudo.mirror.models import Attribute, Tenant
from skudo.rules.concepts import inferir_sinonimos, sembrar
from skudo.rules.models import ConceptMap


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _attr(session, tenant, code, frontend_input="text"):
    session.add(Attribute(
        tenant_id=tenant.id, code=code, label=code, frontend_input=frontend_input,
        declared_scope="global", is_filterable=False, is_required=False,
        attribute_set_ids=[4],
    ))
    session.flush()


def test_el_seed_mapea_los_universales_de_google_con_confianza_uno(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "name")
    _attr(db_session, t, "description")
    _attr(db_session, t, "price")
    _attr(db_session, t, "image")

    creados = sembrar(db_session, t.id)
    por_canonico = {c.canonical: c for c in creados}
    assert por_canonico["title"].attribute_code == "name"
    assert por_canonico["title"].confidence == 1.0
    assert por_canonico["title"].origin == "curada"


def test_el_seed_no_mapea_un_universal_que_el_tenant_no_tiene(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "name")  # sólo name; falta description, price, image
    creados = sembrar(db_session, t.id)
    canonicos = {c.canonical for c in creados}
    assert "title" in canonicos
    assert "description" not in canonicos, "no se inventa un mapeo a un atributo ausente"


def test_sembrar_es_idempotente(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "name")
    sembrar(db_session, t.id)
    sembrar(db_session, t.id)  # segunda vez no duplica
    filas = db_session.query(ConceptMap).filter_by(tenant_id=t.id, canonical="title").all()
    assert len(filas) == 1


def test_infiere_colour_como_sinonimo_de_color(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "color")
    _attr(db_session, t, "colour")
    creados = inferir_sinonimos(db_session, t.id)
    par = [c for c in creados if c.canonical == "color" and c.attribute_code == "colour"]
    assert len(par) == 1
    assert par[0].relation == "sinonimo"
    assert par[0].origin == "inferida"
    assert par[0].confidence < 1.0, "un sinónimo inferido nace para revisión"


def test_no_infiere_un_falso_sinonimo_por_prefijo(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "precio")
    _attr(db_session, t, "precio_especial")
    creados = inferir_sinonimos(db_session, t.id)
    # NO son el mismo concepto: uno es una variante, no un sinónimo.
    assert not any(
        {c.attribute_code for c in creados} >= {"precio", "precio_especial"}
        and c.canonical == "precio"
        for c in creados
    )


def test_la_autoasignacion_canonica_es_curada_no_inferida(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "color")
    _attr(db_session, t, "colour")
    creados = inferir_sinonimos(db_session, t.id)

    # El mapeo canónico color→color es cierto (el atributo color implementa color)
    # así que debe ser origin="curada", no "inferida"
    canonico = [c for c in creados if c.canonical == "color" and c.attribute_code == "color"]
    assert len(canonico) == 1
    assert canonico[0].origin == "curada"
    assert canonico[0].confidence == 1.0

    # El sinónimo colour→color es inferido y debe tener confidence < 1.0
    sinonimo = [c for c in creados if c.canonical == "color" and c.attribute_code == "colour"]
    assert len(sinonimo) == 1
    assert sinonimo[0].origin == "inferida"
    assert sinonimo[0].confidence < 1.0
