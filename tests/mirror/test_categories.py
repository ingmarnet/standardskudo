import pytest
from sqlalchemy import select

from skudo.mirror.categories import (
    CategoryEffect,
    assign_product,
    derive_category_effect,
    set_category_store_state,
    upsert_category,
)
from skudo.mirror.models import ProductCategoryAssignment, Tenant

# Escenario: categoría 15 cuelga del árbol 2 (root de PY). El árbol de BR es 3.
PATH_UNDER_PY_ROOT = [1, 2, 15]


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_effective_when_all_four_conditions_hold():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=2,
        is_active_in_store=True, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is True


def test_not_effective_when_category_is_outside_the_store_root():
    """La asignación existe, pero la categoría no cuelga del árbol de esa tienda."""
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=3,
        is_active_in_store=True, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "fuera_del_arbol_de_la_tienda"


def test_not_effective_when_category_is_inactive_in_that_store():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=2,
        is_active_in_store=False, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "categoria_inactiva_en_la_tienda"


def test_not_effective_when_product_is_not_in_the_store_website():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=2,
        is_active_in_store=True, product_website_ids=[2], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "producto_fuera_del_website"


def test_assignment_is_stored_without_store_scope(db_session, tenant):
    """La tabla de asignación NO lleva store view: en Magento es global."""
    upsert_category(db_session, tenant.id, 15, PATH_UNDER_PY_ROOT, "Climatización")
    assign_product(db_session, tenant.id, "SKU1", 15)

    rows = db_session.scalars(
        select(ProductCategoryAssignment).where(
            ProductCategoryAssignment.tenant_id == tenant.id
        )
    ).all()
    assert len(rows) == 1
    assert not hasattr(rows[0], "store_view_magento_id")


def test_category_name_and_activity_are_per_store_view(db_session, tenant):
    upsert_category(db_session, tenant.id, 15, PATH_UNDER_PY_ROOT, "Climatización")
    set_category_store_state(db_session, tenant.id, 15, 1, True, "Climatización")
    set_category_store_state(db_session, tenant.id, 15, 2, False, "Climatização")

    from skudo.mirror.models import CategoryStoreState

    states = {
        s.store_view_magento_id: (s.is_active, s.name)
        for s in db_session.scalars(
            select(CategoryStoreState).where(CategoryStoreState.tenant_id == tenant.id)
        ).all()
    }
    assert states == {1: (True, "Climatización"), 2: (False, "Climatização")}


def test_the_effect_carries_no_unfilled_identity_fields():
    """`category_magento_id` y `store_view_magento_id` eran `int = 0` que ningún
    código rellenaba: el primer consumidor que los leyera recibiría un cero de
    aspecto confiable. Quien los necesite debe pasarlos, no heredar un default."""
    assert "category_magento_id" not in CategoryEffect.model_fields
    assert "store_view_magento_id" not in CategoryEffect.model_fields
