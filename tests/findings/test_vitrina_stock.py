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
    # is_in_stock None: como si la feature no existiera -> publicado y pleno.
    f = _f(is_in_stock=None, muestra_sin_stock=False)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "pleno"


def test_ignorancia_config_desconocida_no_oculta():
    # sin stock explícito pero config desconocida: gana la ignorancia -> publicado.
    f = _f(is_in_stock=False, muestra_sin_stock=None)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "pleno"


def test_no_navegable_no_se_toca_por_stock():
    f = Ficha(sku="X", attributes={"status": "1", "visibility": "1"},
              is_in_stock=False, muestra_sin_stock=False)
    assert f.estado != OCULTO_SIN_STOCK  # sigue siendo NO_NAVEGABLE
