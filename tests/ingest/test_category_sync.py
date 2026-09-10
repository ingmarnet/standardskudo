"""Ingestor de categorías: la mitad que a `full_sync`/Task A3 le faltaba.

Sin esto, `Category.path` y `CategoryStoreState.is_active` nunca se escribían
y `derive_category_effect` —pura y ya probada— era inalcanzable desde datos de
espejo real. La forma del payload es exactamente la que Task A3 documentó en
`task-A3-report.md`.
"""

from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response
from sqlalchemy import select

from skudo.ingest.category_sync import sync_categories
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Category, CategoryStoreState, Tenant

PAGE_1 = {
    "items": [
        {
            "category_id": 252,
            "path": [1, 2, 222, 252],
            "default_name": "Todo por menos de US$ 500",
            "store_states": [
                {"store_id": 1, "is_active": False, "name": "Todo por menos de US$ 500"},
                {"store_id": 3, "is_active": True, "name": "Tudo por menos de US$ 500"},
            ],
        },
    ],
    "next_cursor": "c2t1ZG8xOjI1Mg==",
}
PAGE_2 = {
    "items": [
        {
            "category_id": 300,
            "path": [1, 2, 300],
            "default_name": "Ofertas",
            "store_states": [
                {"store_id": 1, "is_active": True, "name": "Ofertas"},
                {"store_id": 3, "is_active": True, "name": "Ofertas BR"},
            ],
        },
    ],
    "next_cursor": None,
}


def make_source(tenant_id: int) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/categories"):
            cursor = request.url.params.get("cursor")
            return skudo_response(PAGE_2 if cursor else PAGE_1)
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


def test_sync_categories_walks_every_page(db_session, tenant):
    report = sync_categories(db_session, make_source(tenant.id), store_view_ids=[1, 3])

    assert report.pages_fetched == 2
    assert report.categories_written == 2


def test_the_path_is_mirrored_as_a_list_of_ints(db_session, tenant):
    sync_categories(db_session, make_source(tenant.id), store_view_ids=[1, 3])

    row = db_session.scalar(
        select(Category).where(Category.tenant_id == tenant.id, Category.magento_id == 252)
    )
    assert row.path == [1, 2, 222, 252]
    assert row.default_name == "Todo por menos de US$ 500"


def test_each_store_state_entry_becomes_a_per_store_row(db_session, tenant):
    """El caso real documentado en Task A3: la 252 está inactiva en PY y activa
    en BR, con nombres distintos. Si el ingestor colapsara ambas entradas en un
    solo estado (por ejemplo, quedándose solo con la última), esta prueba lo
    detectaría porque los valores de PY y BR divergen a propósito."""
    sync_categories(db_session, make_source(tenant.id), store_view_ids=[1, 3])

    states = {
        s.store_view_magento_id: (s.is_active, s.name)
        for s in db_session.scalars(
            select(CategoryStoreState).where(
                CategoryStoreState.tenant_id == tenant.id,
                CategoryStoreState.category_magento_id == 252,
            )
        ).all()
    }
    assert states == {
        1: (False, "Todo por menos de US$ 500"),
        3: (True, "Tudo por menos de US$ 500"),
    }


def test_sync_categories_is_idempotent(db_session, tenant):
    sync_categories(db_session, make_source(tenant.id), store_view_ids=[1, 3])
    second = sync_categories(db_session, make_source(tenant.id), store_view_ids=[1, 3])

    total_categories = db_session.scalar(
        select(Category).where(Category.tenant_id == tenant.id)
    )
    assert second.categories_written == 2
    assert total_categories is not None

    from sqlalchemy import func

    category_count = db_session.scalar(
        select(func.count()).select_from(Category).where(Category.tenant_id == tenant.id)
    )
    state_count = db_session.scalar(
        select(func.count()).select_from(CategoryStoreState).where(
            CategoryStoreState.tenant_id == tenant.id
        )
    )
    assert category_count == 2
    assert state_count == 4  # 2 categorías * 2 store views cada una


def test_a_second_sync_updates_rather_than_duplicates_when_data_changes(db_session, tenant):
    sync_categories(db_session, make_source(tenant.id), store_view_ids=[1, 3])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/categories"):
            cursor = request.url.params.get("cursor")
            if cursor:
                return skudo_response({"items": [], "next_cursor": None})
            changed_page = {
                "items": [
                    {
                        "category_id": 252,
                        "path": [1, 2, 222, 252],
                        "default_name": "Todo por menos de US$ 500",
                        "store_states": [
                            {"store_id": 1, "is_active": True, "name": "Reactivada"},
                            {"store_id": 3, "is_active": True, "name": "Tudo por menos de US$ 500"},
                        ],
                    }
                ],
                "next_cursor": None,
            }
            return skudo_response(changed_page)
        return httpx.Response(404)

    source = TenantSource.from_tenant(
        tenant, token="t",
        transport=httpx.MockTransport(handler),
    )
    sync_categories(db_session, source, store_view_ids=[1, 3])

    row = db_session.scalar(
        select(CategoryStoreState).where(
            CategoryStoreState.tenant_id == tenant.id,
            CategoryStoreState.category_magento_id == 252,
            CategoryStoreState.store_view_magento_id == 1,
        )
    )
    assert row.is_active is True
    assert row.name == "Reactivada"
