from skudo.mirror.models import (
    Attribute,
    Category,
    ProductCategoryAssignment,
    ProductRecord,
    Tenant,
)
from skudo.rules.concepts import sembrar
from skudo.rules.floor import cargar_seed, generar_piso
from skudo.rules.models import GoogleFloor


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def test_cargar_seed_puebla_google_floor_una_vez(db_session):
    n1 = cargar_seed(db_session)
    assert n1 > 0
    n2 = cargar_seed(db_session)  # idempotente: no re-inserta
    assert n2 == 0
    assert db_session.query(GoogleFloor).count() == n1


def test_los_universales_aplican_a_cualquier_categoria_mapeada(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    # un atributo name para que el concepto title exista
    db_session.add(Attribute(tenant_id=t.id, code="name", label="name",
                             frontend_input="text", declared_scope="global",
                             is_filterable=False, is_required=False, attribute_set_ids=[4]))
    db_session.flush()
    sembrar(db_session, t.id)
    # una categoría con google_category_id_int mapeado, y un producto que cuelga
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 2, 100],
                            default_name="Remeras"))
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={"google_category_id_int": "212"}, attribute_set_id=4,
        type_id="simple", sync_generation=1,
        scope_provenance={}, content_hash="test",
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()

    reglas = generar_piso(db_session, t.id)
    titulos = [r for r in reglas if r.definition.get("google_attribute") == "title"]
    assert titulos, "el universal title aplica"
    r = titulos[0]
    assert r.origin == "piso_externo"
    assert r.status == "aceptada", "el piso nace aceptado, no borrador"
    assert r.scope_kind == "category"
    assert r.definition["espejo_attribute"] == "name", "mapea vía concept_map"


def test_un_requisito_sin_mapeo_de_concepto_nace_aviso(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    # NO sembramos concepto: 'title' no tiene atributo del espejo
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 100],
                            default_name="X"))
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={"google_category_id_int": "212"}, attribute_set_id=4,
        type_id="simple", sync_generation=1,
        scope_provenance={}, content_hash="test",
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()
    reglas = generar_piso(db_session, t.id)
    r = next(r for r in reglas if r.definition.get("google_attribute") == "title")
    assert r.status == "aviso"
    assert r.definition["sin_mapeo"] is True


def test_una_categoria_sin_mapear_no_produce_piso(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 100],
                            default_name="Sin mapear"))
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={},  # sin google_category_id_int
        attribute_set_id=4, type_id="simple", sync_generation=1,
        scope_provenance={}, content_hash="test",
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()
    reglas = generar_piso(db_session, t.id)
    assert reglas == [], "sin mapeo externo no hay piso; queda desconocido (eje 11 en S1c)"


def test_indumentaria_exige_color_ademas_del_universal(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    for code in ("name", "color"):
        db_session.add(Attribute(tenant_id=t.id, code=code, label=code,
                                 frontend_input="text", declared_scope="global",
                                 is_filterable=False, is_required=False,
                                 attribute_set_ids=[4]))
    db_session.flush()
    sembrar(db_session, t.id)
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 100],
                            default_name="Ropa"))
    # 1604 cae dentro del rango de indumentaria del seed
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={"google_category_id_int": "1604"}, attribute_set_id=4,
        type_id="simple", sync_generation=1,
        scope_provenance={}, content_hash="test",
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()
    reglas = generar_piso(db_session, t.id)
    atributos = {r.definition.get("google_attribute") for r in reglas}
    assert "color" in atributos
    assert "title" in atributos
