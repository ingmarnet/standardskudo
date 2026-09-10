import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response, upsert_record

from skudo.ingest.attribute_sync import sync_attributes
from skudo.ingest.full_sync import full_sync
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Tenant
from skudo.mirror.products import get_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

PAGE_1 = {
    "items": [
        {"sku": "0074", "mpn": "ABC-123/B", "model": "X1", "gtin": None,
         "variant_key": None, "attribute_set_id": 4, "type_id": "simple",
         "global_values": {"name": "Notebook", "weight": "2.1"},
         "store_values": {}, "website_ids": [1], "category_ids": [15],
         "updated_at": "2026-09-01 10:00:00"},
    ],
    "next_cursor": "c2t1ZG8xOjc0",
}
PAGE_2 = {
    "items": [
        {"sku": "SKU2", "mpn": None, "model": None, "gtin": None, "variant_key": None,
         "attribute_set_id": 4, "type_id": "simple",
         "global_values": {"name": "Aire Acondicionado", "price": "1000"},
         "store_values": {"name": "Ar Condicionado", "price": "900"},
         "website_ids": [1], "category_ids": [], "updated_at": "2026-09-02 11:00:00"},
    ],
    "next_cursor": None,
}

# Los dos scopes declarados que la instancia de referencia tiene de verdad:
# `name` es de store view (is_global 0) y `price` es de WEBSITE (is_global 2,
# porque `catalog/price/scope` está en Website ahí, como en 24 atributos más).
# Magento persiste los dos escribiendo una fila EAV por store view, así que sin
# este mapa los dos overrides de SKU2 son indistinguibles.
ATTRIBUTES_PAGE = {
    "items": [
        {"code": "name", "label": "Nombre", "frontend_input": "text",
         "declared_scope": "store", "is_filterable": False, "is_required": True,
         "attribute_set_ids": [4], "options": []},
        {"code": "price", "label": "Precio", "frontend_input": "price",
         "declared_scope": "website", "is_filterable": False, "is_required": False,
         "attribute_set_ids": [4], "options": []},
    ],
    "next_cursor": None,
}


def _without_classification(page: dict) -> dict:
    items = [
        {k: v for k, v in item.items() if k not in ("attribute_set_id", "type_id")}
        for item in page["items"]
    ]
    return {**page, "items": items}


def make_source(
    tenant_id: int,
    with_classification: bool = True,
    categories_of_0074: list[int] | None = None,
    updated_at_of_0074: str | None = None,
) -> TenantSource:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())
    page_1 = PAGE_1 if with_classification else _without_classification(PAGE_1)
    page_2 = PAGE_2 if with_classification else _without_classification(PAGE_2)
    if categories_of_0074 is not None:
        page_1 = {
            **page_1,
            "items": [{**page_1["items"][0], "category_ids": categories_of_0074}],
        }
    if updated_at_of_0074 is not None:
        page_1 = {
            **page_1,
            "items": [{**page_1["items"][0], "updated_at": updated_at_of_0074}],
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return skudo_response(environment)
        if request.url.path.endswith("/products"):
            cursor = request.url.params.get("cursor")
            return skudo_response(page_2 if cursor else page_1)
        if request.url.path.endswith("/attributes"):
            cursor = request.url.params.get("cursor")
            return skudo_response(
                {"items": [], "next_cursor": None} if cursor else ATTRIBUTES_PAGE
            )
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="token",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_full_sync_walks_every_page(db_session, tenant):
    report = full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    assert report.pages_fetched == 2
    assert report.records_written == 2


def test_global_value_is_mirrored_with_global_provenance(db_session, tenant):
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.attributes["name"] == "Notebook"
    assert row.scope_provenance["name"] == "global"


def test_store_override_is_mirrored_with_store_provenance(db_session, tenant):
    source = make_source(tenant.id)
    sync_attributes(db_session, source)

    full_sync(db_session, source, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "SKU2", 1)
    assert row.attributes["name"] == "Ar Condicionado"
    assert row.scope_provenance["name"] == "store"


def test_a_website_scoped_override_is_mirrored_as_website_not_store(db_session, tenant):
    """H2: `price` y `name` llegan con la MISMA forma en el payload —fila EAV de
    store view para los dos— y tienen que salir con procedencias distintas.
    Es la aserción que discrimina: mientras `resolve_scope` emitía dos valores,
    los 24 atributos de website de este catálogo se reportaban como "store", y
    S3 heredaría una respuesta con confianza equivocada sobre dónde corregir.
    """
    source = make_source(tenant.id)
    sync_attributes(db_session, source)

    full_sync(db_session, source, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "SKU2", 1)
    assert row.attributes["price"] == "900"
    assert row.scope_provenance["price"] == "website"
    assert row.scope_provenance["name"] == "store"


def test_an_override_of_an_attribute_that_is_not_mirrored_stays_unknown(db_session, tenant):
    """Sin `sync_attributes`, no hay mapa de scopes: la procedencia de los
    overrides es DESCONOCIDA, nunca "store" inventado. Es también lo que hace
    reprobar al criterio `procedencia_de_scope` cuando alguien corre la ingesta
    de productos sin la de atributos."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    row = get_record(db_session, tenant.id, "SKU2", 1)
    assert row.scope_provenance["name"] == "desconocido"
    # Lo heredado del scope global no depende del mapa: eso se sabe siempre.
    assert get_record(db_session, tenant.id, "0074", 1).scope_provenance["name"] == "global"


def test_identity_survives_the_round_trip(db_session, tenant):
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.sku == "0074"
    assert row.mpn == "ABC-123/B"


def test_full_sync_is_idempotent(db_session, tenant):
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])
    second = full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    from sqlalchemy import func, select

    from skudo.mirror.models import ProductRecord

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == tenant.id
        )
    )
    assert second.records_written == 2
    assert total == 2


def test_website_ids_survive_the_round_trip(db_session, tenant):
    """`ProductReader` ya emite `website_ids` (línea 160 del módulo, Task A3);
    hasta ahora `full_sync` lo tiraba a la basura. Sin él,
    `derive_category_effect` no puede evaluar su tercera condición, que es la
    que discrimina PY de BR en el tenant piloto."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.website_ids == [1]


def test_attribute_set_and_type_survive_the_round_trip(db_session, tenant):
    """Sin `attribute_set_id` en el registro, nada aguas abajo puede distinguir
    'este atributo aplica a este producto y está vacío' (defecto) de 'este
    atributo no pertenece al set de este producto' (no_aplica), que es
    exactamente la confusión que el spec nombra como riesgo mayor."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.attribute_set_id == 4
    assert row.type_id == "simple"


def test_an_unreported_attribute_set_is_unknown_not_zero(db_session, tenant):
    """Si la sonda no informa el set, la columna queda NULL. Un 0 sería un id de
    set creíble y falso."""
    full_sync(db_session, make_source(tenant.id, with_classification=False),
              store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.attribute_set_id is None
    assert row.type_id is None


def test_a_full_sync_revokes_a_category_the_product_left(db_session, tenant):
    """La carga completa aplica semántica de conjunto: lo que el payload no trae
    deja de estar asignado. Antes, una recarga completa no reparaba esto."""
    from sqlalchemy import select

    from skudo.mirror.models import ProductCategoryAssignment

    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])
    # El estado de partida: 0074 está en la 15 según PAGE_1.
    assert db_session.scalars(
        select(ProductCategoryAssignment.category_magento_id).where(
            ProductCategoryAssignment.tenant_id == tenant.id,
            ProductCategoryAssignment.sku == "0074",
        )
    ).all() == [15]

    full_sync(db_session, make_source(tenant.id, categories_of_0074=[]),
              store_view_ids=[1])

    assert db_session.scalars(
        select(ProductCategoryAssignment.category_magento_id).where(
            ProductCategoryAssignment.tenant_id == tenant.id,
            ProductCategoryAssignment.sku == "0074",
        )
    ).all() == []


def _seed_stale(db_session, tenant_id, sku, store_id):
    from datetime import UTC, datetime

    from skudo.mirror.products import ProductIdentity

    upsert_record(db_session, tenant_id, store_id, ProductIdentity(sku=sku),
                  {"name": "fantasma"}, {"name": "global"},
                  datetime(2026, 1, 1, tzinfo=UTC))


def test_a_stale_sku_is_dropped_by_the_next_full_sync(db_session, tenant):
    """Una fila que el espejo tiene y el origen ya no ofrece debe desaparecer.
    Sin barrido, `full_sync` no podía soltarla nunca y `reconcile` detectaba una
    deriva que su propio remedio no reparaba."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[1])
    _seed_stale(db_session, tenant.id, "RANCIO", 1)
    assert get_record(db_session, tenant.id, "RANCIO", 1) is not None

    report = full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    assert get_record(db_session, tenant.id, "RANCIO", 1) is None
    assert report.records_deleted == 1
    # Y lo que sí venía del origen sigue ahí.
    assert get_record(db_session, tenant.id, "0074", 1) is not None
    assert get_record(db_session, tenant.id, "SKU2", 1) is not None


def test_the_sweep_only_touches_the_store_views_of_this_pass(db_session, tenant):
    """Barrer la store view 1 no puede llevarse por delante los registros de la
    3, que esta pasada no ha mirado."""
    _seed_stale(db_session, tenant.id, "SOLO_BR", 3)

    report = full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    assert get_record(db_session, tenant.id, "SOLO_BR", 3) is not None
    assert report.records_deleted == 0


def test_the_sweep_does_not_cross_tenants(db_session):
    from skudo.mirror.models import Tenant as TenantModel

    a = TenantModel(code="a", name="A", base_url="https://a.test", token_env_var="X")
    b = TenantModel(code="b", name="B", base_url="https://b.test", token_env_var="Y")
    db_session.add_all([a, b])
    db_session.flush()
    _seed_stale(db_session, b.id, "0074", 1)

    full_sync(db_session, make_source(a.id), store_view_ids=[1])

    assert get_record(db_session, b.id, "0074", 1) is not None


def test_a_poison_pill_date_does_not_abort_the_page(db_session, tenant):
    """Un `0000-00-00 00:00:00` en un producto no puede tirar la página entera.
    Se guarda el registro con la fecha desconocida y se reporta el caso."""
    report = full_sync(
        db_session,
        make_source(tenant.id, updated_at_of_0074="0000-00-00 00:00:00"),
        store_view_ids=[1],
    )

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row is not None
    assert row.magento_updated_at is None
    assert report.records_without_timestamp == 1
    assert report.skus_without_timestamp == ["0074"]
    # El resto de la pasada se guardó igual.
    assert get_record(db_session, tenant.id, "SKU2", 1) is not None
    assert report.records_written == 2


# --- M3: la pasada completa no deja asignaciones de lo que barrió -----------


def _assignment_pairs(db_session, tenant_id: int) -> set[tuple[str, int]]:
    from sqlalchemy import select

    from skudo.mirror.models import ProductCategoryAssignment

    return set(
        db_session.execute(
            select(
                ProductCategoryAssignment.sku,
                ProductCategoryAssignment.category_magento_id,
            ).where(ProductCategoryAssignment.tenant_id == tenant_id)
        ).all()
    )


def _seed_stale_product_with_categories(db_session, tenant_id, sku, store_view, ids):
    from datetime import UTC, datetime

    from skudo_testing import set_product_categories

    from skudo.mirror.products import ProductIdentity

    upsert_record(
        db_session, tenant_id, store_view, ProductIdentity(sku=sku),
        {"name": sku}, {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC),
    )
    set_product_categories(db_session, tenant_id, sku, ids)
    db_session.flush()


def test_the_sweep_also_drops_the_category_assignments_of_what_it_swept(
    db_session, tenant
):
    """El número de M3: en la pasada de escala de H3 el barrido soltó 457.762
    `ProductRecord` y dejó 457.762 asignaciones huérfanas, porque `_sweep`
    borraba una tabla sola."""
    _seed_stale_product_with_categories(db_session, tenant.id, "RANCIO", 1, [15, 22])

    report = full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    assert report.records_deleted == 1
    assert report.category_assignments_deleted == 2
    # Y lo que el origen SÍ ofrece conserva su asignación: un barrido que
    # borrara de más pasaría igual el "¿quedó limpio?" de arriba.
    assert _assignment_pairs(db_session, tenant.id) == {("0074", 15)}


def test_a_full_sync_of_one_tenant_leaves_another_tenants_assignments_alone(
    db_session, tenant
):
    """La limpieza es un DELETE sobre una tabla compartida por todos los
    tenants. Se siembra en el otro tenant una asignación IGUALMENTE huérfana:
    si al borrado le faltara el filtro por tenant, se la llevaría también."""
    from skudo_testing import set_product_categories

    other = Tenant(code="otro", name="Otro", base_url="https://y.test", token_env_var="T2")
    db_session.add(other)
    db_session.flush()
    set_product_categories(db_session, other.id, "HUERFANO-DE-B", [15])
    _seed_stale_product_with_categories(db_session, tenant.id, "RANCIO", 1, [15])
    db_session.flush()

    report = full_sync(db_session, make_source(tenant.id), store_view_ids=[1])

    assert report.category_assignments_deleted == 1
    assert _assignment_pairs(db_session, other.id) == {("HUERFANO-DE-B", 15)}
