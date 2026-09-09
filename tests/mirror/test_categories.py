import json
from pathlib import Path

import pytest
from sqlalchemy import select

from skudo.magento.environment import parse_environment
from skudo.mirror.categories import (
    CategoryEffect,
    derive_category_effect,
    set_category_store_state,
    set_product_categories,
    upsert_category,
)
from skudo.mirror.models import ProductCategoryAssignment, Tenant

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Topología verificada del tenant piloto: store views 1 (`py`, website 1) y
# 3 (`br`, website 2), AMBAS con root_category_id = 2. La categoría 15 cuelga de
# ese árbol único, así que la condición de árbol vale igual para las dos tiendas.
PATH_UNDER_THE_SHARED_ROOT = [1, 2, 15]
PILOT_ROOT = 2
PY_WEBSITE = 1
BR_WEBSITE = 2


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_effective_when_all_four_conditions_hold():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT, root_category_id=2,
        is_active_in_store=True, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is True


def test_not_effective_when_category_is_outside_the_store_root():
    """La asignación existe, pero la categoría no cuelga del árbol de esa tienda.
    En el tenant piloto ninguna de las dos tiendas está en este caso —comparten
    root—, pero un tenant con un root por tienda sí lo estaría."""
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT, root_category_id=47,
        is_active_in_store=True, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "fuera_del_arbol_de_la_tienda"


def test_not_effective_when_category_is_inactive_in_that_store():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT, root_category_id=2,
        is_active_in_store=False, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "categoria_inactiva_en_la_tienda"


def test_not_effective_when_product_is_not_in_the_store_website():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT, root_category_id=2,
        is_active_in_store=True, product_website_ids=[2], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "producto_fuera_del_website"


def test_assignment_is_stored_without_store_scope(db_session, tenant):
    """La tabla de asignación NO lleva store view: en Magento es global."""
    upsert_category(db_session, tenant.id, 15, PATH_UNDER_THE_SHARED_ROOT, "Climatización")
    set_product_categories(db_session, tenant.id, "SKU1", [15])

    rows = db_session.scalars(
        select(ProductCategoryAssignment).where(
            ProductCategoryAssignment.tenant_id == tenant.id
        )
    ).all()
    assert len(rows) == 1
    assert not hasattr(rows[0], "store_view_magento_id")


def test_category_name_and_activity_are_per_store_view(db_session, tenant):
    upsert_category(db_session, tenant.id, 15, PATH_UNDER_THE_SHARED_ROOT, "Climatización")
    set_category_store_state(db_session, tenant.id, 15, 1, True, "Climatización")
    set_category_store_state(db_session, tenant.id, 15, 3, False, "Climatização")

    from skudo.mirror.models import CategoryStoreState

    states = {
        s.store_view_magento_id: (s.is_active, s.name)
        for s in db_session.scalars(
            select(CategoryStoreState).where(CategoryStoreState.tenant_id == tenant.id)
        ).all()
    }
    assert states == {1: (True, "Climatización"), 3: (False, "Climatização")}


def test_the_effect_carries_no_unfilled_identity_fields():
    """`category_magento_id` y `store_view_magento_id` eran `int = 0` que ningún
    código rellenaba: el primer consumidor que los leyera recibiría un cero de
    aspecto confiable. Quien los necesite debe pasarlos, no heredar un default."""
    assert "category_magento_id" not in CategoryEffect.model_fields
    assert "store_view_magento_id" not in CategoryEffect.model_fields


def test_the_tree_condition_cannot_discriminate_py_from_br_in_the_pilot_tenant():
    """Las dos store views del tenant piloto comparten root, así que la primera
    condición de `derive_category_effect` da el mismo veredicto para las dos:
    si se confiara solo en el árbol, un producto solo-PY parecería visible en BR."""
    profile = parse_environment(
        json.loads((FIXTURES / "environment_opensource.json").read_text())
    )
    assert profile.root_category_id_for(1) == profile.root_category_id_for(3) == PILOT_ROOT


def test_the_website_is_the_discriminating_condition_in_the_pilot_tenant():
    """Un producto asignado solo al website de PY: misma categoría, mismo árbol,
    activa en las dos tiendas, y aun así no aparece en BR. El motivo devuelto
    tiene que ser el website, no un 'fuera del árbol' que sería falso."""
    profile = parse_environment(
        json.loads((FIXTURES / "environment_opensource.json").read_text())
    )
    only_in_py = [profile.website_id_for(1)]

    py = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT,
        root_category_id=profile.root_category_id_for(1),
        is_active_in_store=True,
        product_website_ids=only_in_py,
        store_website_id=profile.website_id_for(1),
    )
    br = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT,
        root_category_id=profile.root_category_id_for(3),
        is_active_in_store=True,
        product_website_ids=only_in_py,
        store_website_id=profile.website_id_for(3),
    )

    assert py.is_effective is True
    assert br.is_effective is False
    assert br.reason == "producto_fuera_del_website"


def test_per_store_activity_also_discriminates_within_the_shared_tree():
    """La otra condición que sí distingue las dos tiendas del tenant piloto:
    la misma categoría puede estar desactivada en una y activa en la otra."""
    profile = parse_environment(
        json.loads((FIXTURES / "environment_opensource.json").read_text())
    )
    in_both_websites = [profile.website_id_for(1), profile.website_id_for(3)]

    br = derive_category_effect(
        assignment_path=PATH_UNDER_THE_SHARED_ROOT,
        root_category_id=profile.root_category_id_for(3),
        is_active_in_store=False,
        product_website_ids=in_both_websites,
        store_website_id=profile.website_id_for(3),
    )
    assert br.is_effective is False
    assert br.reason == "categoria_inactiva_en_la_tienda"


def _assigned(session, tenant_id, sku) -> list[int]:
    return sorted(
        session.scalars(
            select(ProductCategoryAssignment.category_magento_id).where(
                ProductCategoryAssignment.tenant_id == tenant_id,
                ProductCategoryAssignment.sku == sku,
            )
        ).all()
    )


def test_a_category_the_product_left_is_revoked(db_session, tenant):
    """El payload es la verdad completa del conjunto. Sin contraparte del alta,
    un producto que sale de Secarropas queda en Secarropas para siempre, incluso
    tras una carga completa."""
    set_product_categories(db_session, tenant.id, "SKU1", [15, 20])
    assert _assigned(db_session, tenant.id, "SKU1") == [15, 20]

    set_product_categories(db_session, tenant.id, "SKU1", [20])
    assert _assigned(db_session, tenant.id, "SKU1") == [20]


def test_a_product_with_no_categories_ends_with_none(db_session, tenant):
    set_product_categories(db_session, tenant.id, "SKU1", [15])
    set_product_categories(db_session, tenant.id, "SKU1", [])

    assert _assigned(db_session, tenant.id, "SKU1") == []


def test_revoking_one_product_does_not_touch_another(db_session, tenant):
    set_product_categories(db_session, tenant.id, "SKU1", [15, 20])
    set_product_categories(db_session, tenant.id, "SKU2", [15])
    set_product_categories(db_session, tenant.id, "SKU1", [20])

    assert _assigned(db_session, tenant.id, "SKU1") == [20]
    assert _assigned(db_session, tenant.id, "SKU2") == [15]


def test_revoking_in_one_tenant_does_not_touch_the_other(db_session):
    a = Tenant(code="a", name="A", base_url="https://a.test", token_env_var="X")
    b = Tenant(code="b", name="B", base_url="https://b.test", token_env_var="Y")
    db_session.add_all([a, b])
    db_session.flush()

    set_product_categories(db_session, a.id, "SKU1", [15, 20])
    set_product_categories(db_session, b.id, "SKU1", [15, 20])
    set_product_categories(db_session, a.id, "SKU1", [20])

    assert _assigned(db_session, a.id, "SKU1") == [20]
    assert _assigned(db_session, b.id, "SKU1") == [15, 20]


def test_setting_the_same_set_twice_is_idempotent(db_session, tenant):
    set_product_categories(db_session, tenant.id, "SKU1", [15, 20])
    set_product_categories(db_session, tenant.id, "SKU1", [20, 15])

    assert _assigned(db_session, tenant.id, "SKU1") == [15, 20]
