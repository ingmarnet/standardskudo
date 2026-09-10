"""M3, clase 1 — asignaciones producto-categoría de SKUs que ya no están.

`product_category_assignment` es la tabla que los detectores del eje 2 de S1
leen ("producto en una categoría en la que no debería estar",
"sobre-categorizado", "sin categoría"). Una fila cuyo `(tenant_id, sku)` no
tiene `product_record` describe un producto que el espejo ya no contiene: dar
de comer eso a S1 es que el sistema FABRIQUE hallazgos sobre productos
inexistentes, que es el riesgo que la sección 1 del spec nombra como el mayor
de todo el producto.

La limpieza es REFERENCIAL y no por sello de generación: la asignación es
GLOBAL (sin store view), así que un sello por pasada de store view sellaría lo
mismo dos veces y una pasada a medias dejaría asignaciones vivas sin sellar.
El predicado "no existe `product_record` de este tenant con este sku" es
verdadero o falso con independencia de qué pasada escribió la fila, así que no
necesita —ni admite— la puerta de `IncompletePassSweep`: no puede borrar la
asignación de un producto que el espejo tiene. Las dos pruebas que sostienen
esa afirmación son
`test_an_assignment_of_a_product_still_mirrored_in_one_store_view_survives`
(demasiado AMPLIO) y las dos de aislamiento por tenant.
"""

from datetime import UTC, datetime

import pytest
from skudo_testing import set_product_categories, upsert_record
from sqlalchemy import func, select

from skudo.mirror.categories import delete_orphan_category_assignments
from skudo.mirror.models import ProductCategoryAssignment, Tenant
from skudo.mirror.products import ProductIdentity

MIRRORED_AT = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def other_tenant(db_session):
    row = Tenant(code="otro", name="Otro", base_url="https://y.test", token_env_var="T2")
    db_session.add(row)
    db_session.flush()
    return row


def _seed_record(db_session, tenant_id: int, sku: str, store_view: int) -> None:
    upsert_record(
        db_session, tenant_id, store_view, ProductIdentity(sku=sku),
        {"name": sku}, {"name": "global"}, MIRRORED_AT,
    )


def _assignments(db_session, tenant_id: int) -> set[tuple[str, int]]:
    return set(
        db_session.execute(
            select(
                ProductCategoryAssignment.sku,
                ProductCategoryAssignment.category_magento_id,
            ).where(ProductCategoryAssignment.tenant_id == tenant_id)
        ).all()
    )


def test_an_assignment_whose_sku_has_no_product_record_is_deleted(db_session, tenant):
    """El caso medido en la pasada de escala de H3: el barrido de `full_sync`
    borró 457.762 `ProductRecord` y dejó 457.762 asignaciones en pie."""
    set_product_categories(db_session, tenant.id, "BARRIDO", [15, 22])

    deleted = delete_orphan_category_assignments(db_session, tenant.id)

    assert deleted == 2
    assert _assignments(db_session, tenant.id) == set()


def test_a_live_products_assignments_are_untouched(db_session, tenant):
    _seed_record(db_session, tenant.id, "VIVO", 1)
    set_product_categories(db_session, tenant.id, "VIVO", [15])

    assert delete_orphan_category_assignments(db_session, tenant.id) == 0
    assert _assignments(db_session, tenant.id) == {("VIVO", 15)}


def test_an_assignment_of_a_product_still_mirrored_in_one_store_view_survives(
    db_session, tenant
):
    """LA prueba de "demasiado amplio". La asignación es global y el registro
    de producto es por store view: un producto que el origen retiró de PY pero
    sigue ofreciendo en BR conserva su fila de BR, así que su asignación NO es
    huérfana. Una limpieza que borrara "las asignaciones de los SKUs sin fila
    en ESTA store view" —o que corriera a mitad de una pasada por tiendas—
    dejaría al producto de BR sin categorías, y el detector de S1 diría "sin
    categoría" sobre un producto correctamente categorizado.
    """
    _seed_record(db_session, tenant.id, "SOLO-BR", 3)
    set_product_categories(db_session, tenant.id, "SOLO-BR", [15])

    assert delete_orphan_category_assignments(db_session, tenant.id) == 0
    assert _assignments(db_session, tenant.id) == {("SOLO-BR", 15)}


def test_cleaning_one_tenant_never_deletes_another_tenants_assignments(
    db_session, tenant, other_tenant
):
    """Un barrido es un DELETE: sin el filtro por tenant en el borrado, limpiar
    el espejo de un cliente se lleva el catálogo del otro. Se siembra el MISMO
    sku huérfano en los dos tenants, así que la forma débil —"el otro está
    vacío"— no puede pasar por casualidad.
    """
    set_product_categories(db_session, tenant.id, "HUERFANO", [15])
    set_product_categories(db_session, other_tenant.id, "HUERFANO", [15])
    _seed_record(db_session, other_tenant.id, "VIVO-DE-B", 1)
    set_product_categories(db_session, other_tenant.id, "VIVO-DE-B", [22])

    deleted = delete_orphan_category_assignments(db_session, tenant.id)

    assert deleted == 1
    assert _assignments(db_session, tenant.id) == set()
    # Intacto: la huérfana del otro tenant sigue siendo problema del otro
    # tenant, y su asignación viva no se toca.
    assert _assignments(db_session, other_tenant.id) == {
        ("HUERFANO", 15),
        ("VIVO-DE-B", 22),
    }


def test_a_record_of_another_tenant_does_not_make_an_orphan_look_alive(
    db_session, tenant, other_tenant
):
    """La otra dirección del filtro por tenant, la que hace la limpieza
    demasiado ESTRECHA: si el `NOT EXISTS` no filtrara por tenant, el
    `product_record` del tenant B con el mismo sku haría parecer vivo al
    huérfano del tenant A, y la fila rancia sobreviviría para siempre.
    """
    set_product_categories(db_session, tenant.id, "COMPARTIDO", [15])
    _seed_record(db_session, other_tenant.id, "COMPARTIDO", 1)

    assert delete_orphan_category_assignments(db_session, tenant.id) == 1
    assert _assignments(db_session, tenant.id) == set()


def test_the_cleanup_is_idempotent(db_session, tenant):
    set_product_categories(db_session, tenant.id, "HUERFANO", [15])

    assert delete_orphan_category_assignments(db_session, tenant.id) == 1
    assert delete_orphan_category_assignments(db_session, tenant.id) == 0


def test_nothing_is_deleted_in_a_mirror_with_no_orphans(db_session, tenant):
    """Un borrado que no encuentra nada no puede tocar el resto de la tabla."""
    for sku in ("A", "B", "C"):
        _seed_record(db_session, tenant.id, sku, 1)
        set_product_categories(db_session, tenant.id, sku, [15, 22])

    assert delete_orphan_category_assignments(db_session, tenant.id) == 0
    assert db_session.scalar(
        select(func.count()).select_from(ProductCategoryAssignment)
    ) == 6
