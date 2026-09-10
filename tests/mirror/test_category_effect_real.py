"""El enlace roto, cerrado de punta a punta con datos de espejo real.

`derive_category_effect` es pura, ya probada, y hasta esta tarea inalcanzable
desde datos reales por dos huecos independientes: `Category.path` y
`CategoryStoreState.is_active` no se escribían (los cierra
`sync_categories`, Task A4), y `ProductRecord.website_ids` no existía (Task
A4 también). Este test no le pasa nada a mano a `derive_category_effect`:
arma la topología real vía `full_sync` + `sync_categories` y lee cada
argumento de las tablas del espejo.

Topología verificada del tenant piloto (ver `task-A3-report.md`): store views
1 (`py`, website 1) y 3 (`br`, website 2), AMBAS con `root_category_id = 2`.
El árbol NO puede distinguir PY de BR en este tenant — lo hacen
`is_active` por store y el website del producto, que es justo lo que este
test demuestra.
"""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from skudo.ingest.category_sync import sync_categories
from skudo.ingest.full_sync import full_sync
from skudo.ingest.source import TenantSource
from skudo.mirror.categories import derive_category_effect
from skudo.mirror.models import (
    Category,
    CategoryStoreState,
    ProductRecord,
    StoreGroup,
    StoreView,
    Tenant,
)
from skudo.mirror.topology import root_category_id

FIXTURES = Path(__file__).parent.parent / "fixtures"

PY_STORE = 1
BR_STORE = 3

# Categoría 252: el caso real documentado en Task A3. Inactiva en PY, activa
# en BR. El producto está en los dos websites, así que solo `is_active` por
# tienda discrimina aquí.
CATEGORY_INACTIVE_IN_PY = 252
# Categoría 300: activa en las dos tiendas. El producto que la lleva solo está
# en el website de PY, así que solo el website discrimina aquí.
CATEGORY_ACTIVE_EVERYWHERE = 300

CATEGORIES_PAGE = {
    "items": [
        {
            "category_id": CATEGORY_INACTIVE_IN_PY,
            "path": [1, 2, 222, 252],
            "default_name": "Todo por menos de US$ 500",
            "store_states": [
                {"store_id": PY_STORE, "is_active": False,
                 "name": "Todo por menos de US$ 500"},
                {"store_id": BR_STORE, "is_active": True,
                 "name": "Tudo por menos de US$ 500"},
            ],
        },
        {
            "category_id": CATEGORY_ACTIVE_EVERYWHERE,
            "path": [1, 2, 300],
            "default_name": "Ofertas",
            "store_states": [
                {"store_id": PY_STORE, "is_active": True, "name": "Ofertas"},
                {"store_id": BR_STORE, "is_active": True, "name": "Ofertas BR"},
            ],
        },
    ],
    "next_cursor": None,
}

PRODUCTS_PAGE = {
    "items": [
        {
            "sku": "BOTH_WEBSITES", "mpn": None, "model": None, "gtin": None,
            "variant_key": None, "attribute_set_id": 4, "type_id": "simple",
            "global_values": {"name": "En 252, en los dos websites"},
            "store_values": {},
            "website_ids": [1, 2], "category_ids": [CATEGORY_INACTIVE_IN_PY],
            "updated_at": "2026-09-01 10:00:00",
        },
        {
            "sku": "PY_ONLY", "mpn": None, "model": None, "gtin": None,
            "variant_key": None, "attribute_set_id": 4, "type_id": "simple",
            "global_values": {"name": "En 300, solo website PY"},
            "store_values": {},
            "website_ids": [1], "category_ids": [CATEGORY_ACTIVE_EVERYWHERE],
            "updated_at": "2026-09-01 10:00:00",
        },
    ],
    "next_cursor": None,
}


def make_source(tenant_id: int) -> TenantSource:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if path.endswith("/products"):
            return httpx.Response(200, json=PRODUCTS_PAGE)
        if path.endswith("/categories"):
            return httpx.Response(200, json=CATEGORIES_PAGE)
        return httpx.Response(404)

    return TenantSource(
        tenant_id=tenant_id, base_url="https://x.test", token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def _website_id_of_store(session, tenant_id: int, store_view_id: int) -> int:
    """Simétrico a `topology.root_category_id`: la store view hereda el
    website de su grupo, no lo conoce directamente."""
    group_magento_id = session.scalar(
        select(StoreView.group_magento_id).where(
            StoreView.tenant_id == tenant_id, StoreView.magento_id == store_view_id
        )
    )
    return session.scalar(
        select(StoreGroup.website_magento_id).where(
            StoreGroup.tenant_id == tenant_id, StoreGroup.magento_id == group_magento_id
        )
    )


def _category_path(session, tenant_id: int, category_magento_id: int) -> list[int]:
    return session.scalar(
        select(Category.path).where(
            Category.tenant_id == tenant_id, Category.magento_id == category_magento_id
        )
    )


def _is_active(session, tenant_id: int, category_magento_id: int, store_view_id: int) -> bool:
    return session.scalar(
        select(CategoryStoreState.is_active).where(
            CategoryStoreState.tenant_id == tenant_id,
            CategoryStoreState.category_magento_id == category_magento_id,
            CategoryStoreState.store_view_magento_id == store_view_id,
        )
    )


def _product_website_ids(session, tenant_id: int, sku: str, store_view_id: int) -> list[int]:
    return session.scalar(
        select(ProductRecord.website_ids).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.sku == sku,
            ProductRecord.store_view_magento_id == store_view_id,
        )
    )


def _effect_from_mirror(session, tenant_id, sku, category_magento_id, store_view_id):
    """Arma los cinco argumentos de `derive_category_effect` leyendo SOLO el
    espejo: nada aquí se pasa a mano desde el test."""
    return derive_category_effect(
        assignment_path=_category_path(session, tenant_id, category_magento_id),
        root_category_id=root_category_id(session, tenant_id, store_view_id),
        is_active_in_store=_is_active(session, tenant_id, category_magento_id, store_view_id),
        product_website_ids=_product_website_ids(session, tenant_id, sku, store_view_id),
        store_website_id=_website_id_of_store(session, tenant_id, store_view_id),
    )


def test_the_tree_is_identical_for_both_store_views_in_this_tenant(db_session, tenant):
    """Confirma la premisa del brief antes de probar lo que sí discrimina: las
    dos store views del tenant piloto comparten root, leído del espejo (no del
    payload de entorno crudo)."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[PY_STORE, BR_STORE])

    assert root_category_id(db_session, tenant.id, PY_STORE) == 2
    assert root_category_id(db_session, tenant.id, BR_STORE) == 2


def test_per_store_activity_discriminates_when_the_tree_cannot(db_session, tenant):
    """Categoría 252: inactiva en PY, activa en BR. El producto está en los
    dos websites, así que el website NO puede ser lo que la reprueba en PY —
    si lo fuera, esta prueba lo detectaría porque esperamos exactamente
    'categoria_inactiva_en_la_tienda', no 'producto_fuera_del_website'."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[PY_STORE, BR_STORE])
    sync_categories(db_session, make_source(tenant.id), store_view_ids=[PY_STORE, BR_STORE])

    py = _effect_from_mirror(
        db_session, tenant.id, "BOTH_WEBSITES", CATEGORY_INACTIVE_IN_PY, PY_STORE
    )
    br = _effect_from_mirror(
        db_session, tenant.id, "BOTH_WEBSITES", CATEGORY_INACTIVE_IN_PY, BR_STORE
    )

    assert py.is_effective is False
    assert py.reason == "categoria_inactiva_en_la_tienda"
    assert br.is_effective is True
    assert br.reason == "efectiva"


def test_the_website_discriminates_when_the_tree_and_activity_cannot(db_session, tenant):
    """Categoría 300: activa en las dos tiendas, mismo árbol. El producto solo
    está en el website de PY. Si el ingestor no leyera `website_ids` (el hueco
    que esta tarea cierra), `product_website_ids` sería `None` y
    `derive_category_effect` no podría evaluar esta condición en absoluto."""
    full_sync(db_session, make_source(tenant.id), store_view_ids=[PY_STORE, BR_STORE])
    sync_categories(db_session, make_source(tenant.id), store_view_ids=[PY_STORE, BR_STORE])

    py = _effect_from_mirror(
        db_session, tenant.id, "PY_ONLY", CATEGORY_ACTIVE_EVERYWHERE, PY_STORE
    )
    br = _effect_from_mirror(
        db_session, tenant.id, "PY_ONLY", CATEGORY_ACTIVE_EVERYWHERE, BR_STORE
    )

    assert py.is_effective is True
    assert py.reason == "efectiva"
    assert br.is_effective is False
    assert br.reason == "producto_fuera_del_website"
