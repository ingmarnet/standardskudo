"""La válvula: un barrido que se llevaría la mayoría del espejo se NIEGA.

El modo de fallo que cierra. Una pasada completa que no devuelve nada —un
endpoint que responde `[]` por un bug, un token vencido que igual da 200, una
paginación que se rompe— es indistinguible, para el sello, de "el origen dejó
de ofrecer todo el catálogo": la pasada termina, marca `pass_complete`, y el
barrido borra el espejo entero del tenant. Con las etiquetas de las opciones
adentro, que son el único registro de que "Negro" y "Preto" son la misma
`option_id`.

El espejo es derivado y una resincronización lo reconstruye, así que el daño
está acotado. Lo que no es aceptable es que sea SILENCIOSO, y que el camino
destructivo dispare con más fuerza justo cuando algo aguas arriba se rompió.

La válvula no es un salto silencioso: es un ABORTO con los conteos en el
mensaje y una bandera explícita para seguir adelante. Vaciar un catálogo de
verdad sigue siendo posible; lo que deja de ser posible es que pase solo a las
tres de la mañana.

Cada barrido tiene las DOS pruebas que discriminan una comparación invertida
—el error que haría que la válvula dispare en las pasadas normales y deje pasar
las catastróficas—:

- una pasada normal, que borra una minoría, TIENE que barrer sin bandera;
- una pasada catastrófica, que borra todo, TIENE que negarse sin bandera y
  barrer con ella.

Con el `>` invertido a `<`, la primera falla. Con la válvula quitada, la
segunda. Ninguna de las dos sola alcanza.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response, upsert_record
from sqlalchemy import func, select

from skudo.ingest.attribute_sync import _sweep_attributes, sync_attributes
from skudo.ingest.category_sync import _sweep_categories, sync_categories
from skudo.ingest.full_sync import _sweep, full_sync
from skudo.ingest.source import TenantSource
from skudo.ingest.sweep import (
    MASS_SWEEP_MAX_SHARE,
    MASS_SWEEP_MIN_ROWS,
    MassSweepRefused,
)
from skudo.mirror.models import (
    Attribute,
    AttributeOption,
    AttributeOptionLabel,
    Category,
    CategoryStoreState,
    FullSyncCheckpoint,
    ProductRecord,
    SyncPass,
    Tenant,
)
from skudo.mirror.products import ProductIdentity

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Una cohorte cómodamente por encima del piso de filas de la válvula: con
# menos, la válvula no se aplica y estas pruebas no dirían nada.
COHORT = MASS_SWEEP_MIN_ROWS * 3

# Cuántas filas borra una pasada "normal" en estas pruebas. Tiene que estar por
# ENCIMA del piso —si no, la válvula ni se consulta y la prueba no discrimina
# una comparación invertida— y cómodamente por debajo de la mayoría. Es la
# constante que hace que las pruebas de "esto tiene que barrer" digan algo.
MINORITY = MASS_SWEEP_MIN_ROWS + 1


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


# --- productos --------------------------------------------------------------


def _product(sku: str) -> dict:
    return {
        "sku": sku, "mpn": None, "model": None, "gtin": None, "variant_key": None,
        "attribute_set_id": 4, "type_id": "simple",
        "global_values": {"name": sku}, "store_values": {},
        "website_ids": [1], "category_ids": [], "updated_at": "2026-09-01 10:00:00",
    }


def _catalog_source(tenant_id: int, skus: list[str]) -> TenantSource:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return skudo_response(environment)
        if request.url.path.endswith("/products"):
            return skudo_response(
                {"items": [_product(sku) for sku in skus], "next_cursor": None}
            )
        if request.url.path.endswith("/attributes"):
            return skudo_response({"items": [], "next_cursor": None})
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="token",
        transport=httpx.MockTransport(handler),
    )


def _seed_products(db_session, tenant_id: int, skus: list[str]) -> None:
    for sku in skus:
        upsert_record(
            db_session, tenant_id, 1, ProductIdentity(sku=sku),
            {"name": sku}, {"name": "global"}, None,
        )
    db_session.commit()


def _record_count(db_session, tenant_id: int) -> int:
    return db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id
        )
    )


def test_a_normal_pass_that_drops_a_minority_sweeps_without_the_flag(db_session, tenant):
    """LA prueba que falla si la comparación se invierte.

    Borra 11 filas de 41 —por ENCIMA del piso, así que la válvula sí se
    consulta, y por debajo de la mayoría— que es el caso NORMAL: tiene que
    barrer sin que nadie autorice nada. Una válvula que dispara acá es una
    válvula que alguien apaga.
    """
    live = [f"SKU-{i:03d}" for i in range(COHORT)]
    stale = [f"RANCIO-{i:03d}" for i in range(MINORITY)]
    _seed_products(db_session, tenant.id, [*live, *stale])

    report = full_sync(db_session, _catalog_source(tenant.id, live), store_view_ids=[1])

    assert report.records_deleted == MINORITY
    assert _record_count(db_session, tenant.id) == COHORT


def test_a_pass_that_would_drop_the_whole_catalog_refuses_without_the_flag(
    db_session, tenant
):
    """El origen responde `[]` —el bug, el token vencido, la paginación rota— y
    el barrido se llevaría las 30 filas. Se niega, y el espejo queda INTACTO."""
    _seed_products(db_session, tenant.id, [f"SKU-{i:03d}" for i in range(COHORT)])

    with pytest.raises(MassSweepRefused) as raised:
        full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])

    assert str(COHORT) in str(raised.value)
    assert "--sweep-anyway" in str(raised.value)
    assert _record_count(db_session, tenant.id) == COHORT


def test_the_refusal_leaves_the_pass_retriable_and_not_marked_swept(db_session, tenant):
    """Ni a medio barrer ni marcada como completa-y-barrida: la invocación
    siguiente CONTINÚA la misma generación —no relee el catálogo— y vuelve a
    negarse, hasta que alguien lo autorice."""
    _seed_products(db_session, tenant.id, [f"SKU-{i:03d}" for i in range(COHORT)])
    with pytest.raises(MassSweepRefused):
        full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])

    checkpoint = db_session.scalar(
        select(FullSyncCheckpoint).where(FullSyncCheckpoint.tenant_id == tenant.id)
    )
    assert (checkpoint.pass_complete, checkpoint.swept) == (True, False)
    generation = checkpoint.generation

    # Segunda invocación: misma generación, y la misma negativa.
    with pytest.raises(MassSweepRefused):
        full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])
    assert db_session.scalar(
        select(FullSyncCheckpoint.generation).where(
            FullSyncCheckpoint.tenant_id == tenant.id
        )
    ) == generation
    assert _record_count(db_session, tenant.id) == COHORT


def test_a_legitimate_mass_deletion_goes_through_with_the_flag(db_session, tenant):
    """Un tenant que de verdad vacía su catálogo tiene que poder hacerlo: la
    válvula pide una autorización explícita, no prohíbe."""
    _seed_products(db_session, tenant.id, [f"SKU-{i:03d}" for i in range(COHORT)])
    with pytest.raises(MassSweepRefused):
        full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])

    report = full_sync(
        db_session, _catalog_source(tenant.id, []), store_view_ids=[1], sweep_anyway=True
    )

    assert report.records_deleted == COHORT
    assert _record_count(db_session, tenant.id) == 0


def test_the_valve_does_not_apply_below_the_row_floor(db_session, tenant):
    """El piso existe para que la válvula no moleste donde no puede proteger
    nada: resincronizar un puñado de filas cuesta segundos. Con menos filas
    que el piso, un barrido del 100 % pasa igual."""
    _seed_products(db_session, tenant.id, [f"SKU-{i}" for i in range(MASS_SWEEP_MIN_ROWS)])

    report = full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])

    assert report.records_deleted == MASS_SWEEP_MIN_ROWS


def test_the_sweep_of_one_store_view_is_measured_against_that_store_view(
    db_session, tenant
):
    """La proporción se mide sobre lo que ESE barrido puede borrar, no sobre el
    espejo entero: si se midiera sobre el total del tenant, vaciar PY con BR
    intacto daría 50 % y pasaría inadvertido."""
    _seed_products(db_session, tenant.id, [f"SKU-{i:03d}" for i in range(COHORT)])
    for sku in (f"SKU-{i:03d}" for i in range(COHORT)):
        upsert_record(
            db_session, tenant.id, 3, ProductIdentity(sku=sku),
            {"name": sku}, {"name": "global"}, None,
        )
    db_session.commit()

    with pytest.raises(MassSweepRefused):
        full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])

    assert _record_count(db_session, tenant.id) == COHORT * 2


def test_the_sweep_helper_refuses_on_its_own(db_session, tenant):
    """La válvula vive DENTRO del barrido, como la puerta de pasada completa:
    llamarlo a mano no la esquiva."""
    _seed_products(db_session, tenant.id, [f"SKU-{i:03d}" for i in range(COHORT)])
    with pytest.raises(MassSweepRefused):
        full_sync(db_session, _catalog_source(tenant.id, []), store_view_ids=[1])
    generation = db_session.scalar(
        select(FullSyncCheckpoint.generation).where(
            FullSyncCheckpoint.tenant_id == tenant.id
        )
    )

    with pytest.raises(MassSweepRefused):
        _sweep(db_session, tenant.id, 1, generation)
    assert _record_count(db_session, tenant.id) == COHORT


# --- atributos y categorías -------------------------------------------------


def _attribute(code: str, option_ids: list[int]) -> dict:
    return {
        "code": code, "label": code.title(), "frontend_input": "select",
        "declared_scope": "global", "is_filterable": True, "is_required": False,
        "attribute_set_ids": [4],
        "options": [
            {"option_id": option_id, "labels": {"0": f"o{option_id}"}}
            for option_id in option_ids
        ],
    }


def _category(category_id: int) -> dict:
    return {
        "category_id": category_id, "path": [1, 2, category_id],
        "default_name": f"Cat {category_id}",
        "store_states": [{"store_id": 1, "is_active": True, "name": f"Cat {category_id}"}],
    }


def _pages_source(tenant_id: int, path: str, items: list[dict]) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(path):
            return skudo_response({"items": items, "next_cursor": None})
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="token",
        transport=httpx.MockTransport(handler),
    )


def _counts(db_session, tenant_id: int) -> tuple[int, int, int]:
    def count(model, *where):
        return db_session.scalar(select(func.count()).select_from(model).where(*where))

    return (
        count(Attribute, Attribute.tenant_id == tenant_id),
        count(AttributeOption, AttributeOption.tenant_id == tenant_id),
        db_session.scalar(
            select(func.count())
            .select_from(AttributeOptionLabel)
            .join(AttributeOption, AttributeOption.id == AttributeOptionLabel.option_row_id)
            .where(AttributeOption.tenant_id == tenant_id)
        ),
    )


def test_an_attribute_pass_that_drops_a_minority_of_options_sweeps(db_session, tenant):
    """La mitad "normal" de la válvula de atributos: 11 opciones de 41, por
    encima del piso y por debajo de la mayoría. Con la comparación invertida,
    esta pasada perfectamente ordinaria se negaría."""
    options = list(range(500, 500 + COHORT + MINORITY))
    sync_attributes(db_session, _pages_source(tenant.id, "/attributes",
                                              [_attribute("color", options)]))

    report = sync_attributes(
        db_session,
        _pages_source(tenant.id, "/attributes", [_attribute("color", options[:COHORT])]),
    )

    assert report.options_deleted == MINORITY
    assert _counts(db_session, tenant.id) == (1, COHORT, COHORT)


def test_an_attribute_pass_that_would_drop_every_option_refuses(db_session, tenant):
    """El caso que motiva todo: el endpoint responde `[]` y el barrido se
    llevaría las 30 opciones CON SUS ETIQUETAS, que son la identidad PY/BR."""
    options = list(range(500, 500 + COHORT))
    sync_attributes(db_session, _pages_source(tenant.id, "/attributes",
                                              [_attribute("color", options)]))

    with pytest.raises(MassSweepRefused) as raised:
        sync_attributes(db_session, _pages_source(tenant.id, "/attributes", []))

    assert "attribute_option" in str(raised.value)
    assert _counts(db_session, tenant.id) == (1, COHORT, COHORT)
    row = db_session.scalar(
        select(SyncPass).where(
            SyncPass.tenant_id == tenant.id, SyncPass.pass_kind == "attributes"
        )
    )
    assert (row.pass_complete, row.swept) == (True, False)


def test_an_attribute_mass_deletion_goes_through_with_the_flag(db_session, tenant):
    options = list(range(500, 500 + COHORT))
    sync_attributes(db_session, _pages_source(tenant.id, "/attributes",
                                              [_attribute("color", options)]))

    report = sync_attributes(
        db_session, _pages_source(tenant.id, "/attributes", []), sweep_anyway=True
    )

    assert (report.options_deleted, report.option_labels_deleted) == (COHORT, COHORT)
    assert _counts(db_session, tenant.id) == (0, 0, 0)


def test_the_attribute_sweep_helper_refuses_on_its_own(db_session, tenant):
    options = list(range(500, 500 + COHORT))
    sync_attributes(db_session, _pages_source(tenant.id, "/attributes",
                                              [_attribute("color", options)]))
    with pytest.raises(MassSweepRefused):
        sync_attributes(db_session, _pages_source(tenant.id, "/attributes", []))
    generation = db_session.scalar(
        select(SyncPass.generation).where(
            SyncPass.tenant_id == tenant.id, SyncPass.pass_kind == "attributes"
        )
    )

    with pytest.raises(MassSweepRefused):
        _sweep_attributes(db_session, tenant.id, generation)
    assert _counts(db_session, tenant.id) == (1, COHORT, COHORT)


def _category_counts(db_session, tenant_id: int) -> tuple[int, int]:
    def count(model):
        return db_session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )

    return count(Category), count(CategoryStoreState)


def test_a_category_pass_that_drops_a_minority_sweeps(db_session, tenant):
    """Misma forma que las dos anteriores, y la misma razón para el 11 de 41."""
    categories = [_category(i) for i in range(100, 100 + COHORT + MINORITY)]
    sync_categories(db_session, _pages_source(tenant.id, "/categories", categories), [1])

    report = sync_categories(
        db_session, _pages_source(tenant.id, "/categories", categories[:COHORT]), [1]
    )

    assert report.categories_deleted == MINORITY
    assert _category_counts(db_session, tenant.id) == (COHORT, COHORT)


def test_a_category_pass_that_would_drop_the_whole_tree_refuses(db_session, tenant):
    categories = [_category(i) for i in range(100, 100 + COHORT)]
    sync_categories(db_session, _pages_source(tenant.id, "/categories", categories), [1])

    with pytest.raises(MassSweepRefused) as raised:
        sync_categories(db_session, _pages_source(tenant.id, "/categories", []), [1])

    assert "category" in str(raised.value)
    assert _category_counts(db_session, tenant.id) == (COHORT, COHORT)


def test_a_category_mass_deletion_goes_through_with_the_flag(db_session, tenant):
    categories = [_category(i) for i in range(100, 100 + COHORT)]
    sync_categories(db_session, _pages_source(tenant.id, "/categories", categories), [1])

    report = sync_categories(
        db_session, _pages_source(tenant.id, "/categories", []), [1], sweep_anyway=True
    )

    assert (report.categories_deleted, report.category_store_states_deleted) == (
        COHORT,
        COHORT,
    )
    assert _category_counts(db_session, tenant.id) == (0, 0)


def test_the_category_sweep_helper_refuses_on_its_own(db_session, tenant):
    categories = [_category(i) for i in range(100, 100 + COHORT)]
    sync_categories(db_session, _pages_source(tenant.id, "/categories", categories), [1])
    with pytest.raises(MassSweepRefused):
        sync_categories(db_session, _pages_source(tenant.id, "/categories", []), [1])
    generation = db_session.scalar(
        select(SyncPass.generation).where(
            SyncPass.tenant_id == tenant.id, SyncPass.pass_kind == "categories"
        )
    )

    with pytest.raises(MassSweepRefused):
        _sweep_categories(db_session, tenant.id, generation)
    assert _category_counts(db_session, tenant.id) == (COHORT, COHORT)


# --- la forma del umbral ----------------------------------------------------


def test_the_threshold_is_a_strict_majority(db_session, tenant):
    """Exactamente la mitad NO es "la mayoría": con 30 de 60 el barrido pasa.
    La prueba fija la forma de la comparación (`>`, no `>=`), que es lo que
    separa "la mayoría se va" de "la mitad cambió"."""
    assert MASS_SWEEP_MAX_SHARE == 0.5
    live = [f"SKU-{i:03d}" for i in range(COHORT)]
    _seed_products(db_session, tenant.id, [*live, *[f"VIEJO-{i}" for i in range(COHORT)]])

    report = full_sync(db_session, _catalog_source(tenant.id, live), store_view_ids=[1])

    assert report.records_deleted == COHORT
