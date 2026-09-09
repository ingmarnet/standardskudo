import json
from pathlib import Path

import pytest
from sqlalchemy import select

from skudo.magento.environment import parse_environment
from skudo.mirror.models import StoreView, Tenant
from skudo.mirror.topology import root_category_id, sync_topology

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def profile():
    return parse_environment(json.loads((FIXTURES / "environment_opensource.json").read_text()))


@pytest.fixture
def tenant(db_session):
    row = Tenant(
        code="nissei",
        name="Nissei",
        base_url="https://example.test",
        token_env_var="SKUDO_TENANT_NISSEI_TOKEN",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_sync_stores_the_two_store_views(db_session, tenant, profile):
    sync_topology(db_session, tenant.id, profile)

    codes = db_session.scalars(
        select(StoreView.code).where(StoreView.tenant_id == tenant.id).order_by(StoreView.code)
    ).all()
    assert codes == ["br_pt", "py_es"]


def test_root_category_is_resolved_per_store_view(db_session, tenant, profile):
    sync_topology(db_session, tenant.id, profile)

    assert root_category_id(db_session, tenant.id, 1) == 2
    assert root_category_id(db_session, tenant.id, 2) == 3


def test_sync_is_idempotent(db_session, tenant, profile):
    sync_topology(db_session, tenant.id, profile)
    sync_topology(db_session, tenant.id, profile)

    count = len(
        db_session.scalars(select(StoreView).where(StoreView.tenant_id == tenant.id)).all()
    )
    assert count == 2


def test_topology_is_isolated_per_tenant(db_session, profile):
    a = Tenant(code="a", name="A", base_url="https://a.test", token_env_var="X")
    b = Tenant(code="b", name="B", base_url="https://b.test", token_env_var="Y")
    db_session.add_all([a, b])
    db_session.flush()

    sync_topology(db_session, a.id, profile)

    assert (
        db_session.scalars(select(StoreView).where(StoreView.tenant_id == b.id)).all() == []
    )
