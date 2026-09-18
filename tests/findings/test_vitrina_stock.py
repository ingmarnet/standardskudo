from skudo.findings.catalog import OCULTO_SIN_STOCK, PUBLICADO, Ficha, _publicado

BASE = {"status": "1", "visibility": "4"}  # publicado y navegable


def _f(**kw):
    stock = {k: kw.pop(k) for k in ("is_in_stock", "muestra_sin_stock") if k in kw}
    return Ficha(sku="X", attributes={**BASE}, **stock)


def test_con_stock_es_pleno():
    f = _f(is_in_stock=True, muestra_sin_stock=False)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "pleno"


def test_sin_stock_y_oculto_no_es_publicado():
    f = _f(is_in_stock=False, muestra_sin_stock=False)
    assert f.estado == OCULTO_SIN_STOCK
    assert _publicado(f) is False
    assert f.prioridad_vitrina is None


def test_sin_stock_pero_visible_es_publicado_con_prioridad_menor():
    f = _f(is_in_stock=False, muestra_sin_stock=True)
    assert f.estado == PUBLICADO
    assert _publicado(f) is True
    assert f.prioridad_vitrina == "sin_stock"


def test_ignorancia_stock_desconocido_se_comporta_como_hoy():
    # is_in_stock None: sigue publicado y se sigue puntuando (ignorancia no
    # oculta), pero la prioridad de vitrina declara el desconocido en vez de
    # afirmar "pleno" (spec §6): no hay datos de stock para este producto.
    f = _f(is_in_stock=None, muestra_sin_stock=False)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "desconocido"


def test_ignorancia_config_desconocida_no_oculta():
    # sin stock explícito pero config desconocida: gana la ignorancia -> publicado.
    f = _f(is_in_stock=False, muestra_sin_stock=None)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "sin_stock"


def test_con_stock_true_es_pleno_y_desconocido_no_se_confunde_con_pleno():
    # Bloquea la distinción: is_in_stock=True es 'pleno' (veredicto conocido),
    # is_in_stock=None es 'desconocido' (sin datos) -- nunca el mismo valor.
    conocido = _f(is_in_stock=True, muestra_sin_stock=False)
    desconocido = _f(is_in_stock=None, muestra_sin_stock=False)
    assert conocido.prioridad_vitrina == "pleno"
    assert desconocido.prioridad_vitrina == "desconocido"


def test_no_navegable_no_se_toca_por_stock():
    f = Ficha(sku="X", attributes={"status": "1", "visibility": "1"},
              is_in_stock=False, muestra_sin_stock=False)
    assert f.estado != OCULTO_SIN_STOCK  # sigue siendo NO_NAVEGABLE
