"""M3, clases 2 y 3 — opciones, etiquetas, categorías y estados por tienda.

`sync_attributes` y `sync_categories` documentaban "no hay barrido" como fuera
de alcance: una opción, una categoría o un estado por tienda que el origen
borraba seguía pareciendo vivo en el espejo para siempre. El caso de las
opciones es el que muerde primero, porque la consolidación de S1 decide por
`option_id`: una opción borrada que parece viva es candidata a una corrección
que no apuntaría a nada.

El mecanismo es el de H3 —sello de generación en las filas que la pasada toca,
barrido de lo que no lleva el sello— con la misma puerta: la precondición
`pass_complete` se lee de la BASE dentro del barrido, así que barrer sobre una
pasada a medias no es expresable.

Y acá esa puerta pesa MÁS que para los productos. Un producto barrido de más
vuelve en la próxima sincronización. Una OPCIÓN barrida de más se lleva sus
etiquetas, que son el único registro de que "Negro" y "Preto" son la misma
opción: la identidad que este sub-proyecto existe para proteger. Por eso hay
una prueba con una sincronización deliberadamente interrumpida por cada una de
las dos pasadas, y una que llama al barrido a mano con una generación ajena
—el caso en que el barrido se llevaría TODAS las filas del tenant—.
"""

from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response
from sqlalchemy import func, select

from skudo.ingest.attribute_sync import _sweep_attributes, sync_attributes
from skudo.ingest.category_sync import _sweep_categories, sync_categories
from skudo.ingest.source import TenantSource
from skudo.ingest.sweep import (
    PASS_ATTRIBUTES,
    PASS_CATEGORIES,
    IncompletePassSweep,
)
from skudo.mirror.attributes import distinct_option_ids, option_labels
from skudo.mirror.models import (
    Attribute,
    AttributeOption,
    AttributeOptionLabel,
    Category,
    CategoryStoreState,
    SyncPass,
    Tenant,
)


class Interrupted(RuntimeError):
    """Lo que se le hace estallar al transporte para cortar una pasada."""


# --- dobles del cable -------------------------------------------------------


def _attribute(code: str, option_ids: list[int]) -> dict:
    return {
        "code": code,
        "label": code.title(),
        "frontend_input": "select",
        "declared_scope": "global",
        "is_filterable": True,
        "is_required": False,
        "attribute_set_ids": [4],
        "options": [
            {"option_id": option_id, "labels": {"0": f"opt{option_id}", "3": f"opc{option_id}"}}
            for option_id in option_ids
        ],
    }


def _category(category_id: int, store_ids: list[int]) -> dict:
    return {
        "category_id": category_id,
        "path": [1, 2, category_id],
        "default_name": f"Cat {category_id}",
        "store_states": [
            {"store_id": store_id, "is_active": True, "name": f"Cat {category_id}/{store_id}"}
            for store_id in store_ids
        ],
    }


class FakePages:
    """Sirve páginas de un endpoint por cursor, con corte deliberado a demanda.

    El cursor de la página `i` es `"p{i}"`: un cursor recibido identifica sin
    ambigüedad qué página se pidió, que es lo que permite cortar en la segunda.
    """

    def __init__(self, path: str, pages: list[list[dict]], fail_before_page: int | None = None):
        self.path = path
        self.pages = pages
        self.fail_before_page = fail_before_page
        self.requested_cursors: list[str | None] = []

    def source(self, tenant_id: int) -> TenantSource:
        def handler(request: httpx.Request) -> httpx.Response:
            if not request.url.path.endswith(self.path):
                return httpx.Response(404)
            cursor = request.url.params.get("cursor")
            index = 0 if cursor is None else int(cursor[1:])
            if self.fail_before_page == index:
                raise Interrupted(f"corte deliberado antes de la página {index}")
            self.requested_cursors.append(cursor)
            is_last = index == len(self.pages) - 1
            return skudo_response(
                {
                    "items": self.pages[index],
                    "next_cursor": None if is_last else f"p{index + 1}",
                }
            )

        return TenantSource.from_tenant(
            SimpleNamespace(id=tenant_id, base_url="https://x.test"),
            token="token",
            transport=httpx.MockTransport(handler),
        )


def attributes(pages: list[list[dict]], fail_before_page: int | None = None) -> FakePages:
    return FakePages("/attributes", pages, fail_before_page)


def categories(pages: list[list[dict]], fail_before_page: int | None = None) -> FakePages:
    return FakePages("/categories", pages, fail_before_page)


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


def _pass_row(db_session, tenant_id: int, kind: str) -> SyncPass:
    return db_session.scalar(
        select(SyncPass).where(SyncPass.tenant_id == tenant_id, SyncPass.pass_kind == kind)
    )


def _label_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(AttributeOptionLabel))


def _labels_of_tenant(db_session, tenant_id: int) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(AttributeOptionLabel)
        .join(AttributeOption, AttributeOption.id == AttributeOptionLabel.option_row_id)
        .where(AttributeOption.tenant_id == tenant_id)
    )


def _attribute_codes(db_session, tenant_id: int) -> list[str]:
    return sorted(
        db_session.scalars(
            select(Attribute.code).where(Attribute.tenant_id == tenant_id)
        ).all()
    )


def _category_ids(db_session, tenant_id: int) -> list[int]:
    return sorted(
        db_session.scalars(
            select(Category.magento_id).where(Category.tenant_id == tenant_id)
        ).all()
    )


def _store_states(db_session, tenant_id: int) -> set[tuple[int, int]]:
    return set(
        db_session.execute(
            select(
                CategoryStoreState.category_magento_id,
                CategoryStoreState.store_view_magento_id,
            ).where(CategoryStoreState.tenant_id == tenant_id)
        ).all()
    )


# --- atributos: la pasada completa barre lo que el origen dejó de ofrecer ---


def test_an_option_the_origin_dropped_is_swept_with_its_labels(db_session, tenant):
    sync_attributes(db_session, attributes([[_attribute("color", [500, 501])]]).source(tenant.id))
    assert distinct_option_ids(db_session, tenant.id, "color") == [500, 501]

    report = sync_attributes(
        db_session, attributes([[_attribute("color", [500])]]).source(tenant.id)
    )

    assert distinct_option_ids(db_session, tenant.id, "color") == [500]
    assert report.options_deleted == 1
    assert report.option_labels_deleted == 2
    # La opción que sigue viva conserva sus DOS etiquetas: un barrido que se
    # llevara las etiquetas de más pasaría igual un "¿quedó limpio?".
    assert option_labels(db_session, tenant.id, "color", 500) == {0: "opt500", 3: "opc500"}
    assert _labels_of_tenant(db_session, tenant.id) == 2


def test_an_attribute_the_origin_dropped_is_swept_with_its_options(db_session, tenant):
    """Un atributo borrado arriba se lleva sus opciones porque la pasada ya no
    las nombra, y su propia fila porque tampoco la nombra. Dejarla haría que
    `declared_scopes` —y el detector filter-blind de S1— vieran un atributo
    filtrable que no existe."""
    sync_attributes(
        db_session,
        attributes([[_attribute("color", [500]), _attribute("talle", [700, 701])]]).source(
            tenant.id
        ),
    )

    report = sync_attributes(
        db_session, attributes([[_attribute("color", [500])]]).source(tenant.id)
    )

    assert _attribute_codes(db_session, tenant.id) == ["color"]
    assert distinct_option_ids(db_session, tenant.id, "talle") == []
    assert report.attributes_deleted == 1
    assert report.options_deleted == 2
    assert report.option_labels_deleted == 4


def test_a_complete_pass_that_returns_everything_deletes_nothing(db_session, tenant):
    source = attributes([[_attribute("color", [500, 501])]])
    sync_attributes(db_session, source.source(tenant.id))

    report = sync_attributes(
        db_session, attributes([[_attribute("color", [500, 501])]]).source(tenant.id)
    )

    assert (report.attributes_deleted, report.options_deleted) == (0, 0)
    assert report.option_labels_deleted == 0
    assert distinct_option_ids(db_session, tenant.id, "color") == [500, 501]


def test_the_pass_is_sealed_and_swept_when_it_finishes(db_session, tenant):
    report = sync_attributes(
        db_session, attributes([[_attribute("color", [500])]]).source(tenant.id)
    )

    row = _pass_row(db_session, tenant.id, PASS_ATTRIBUTES)
    assert (row.pass_complete, row.swept) == (True, True)
    assert row.generation == report.generation
    assert row.pages_done == 1


# --- atributos: la interrupción NO barre ------------------------------------


def test_an_interrupted_attribute_pass_does_not_sweep(db_session, tenant):
    """LA prueba de esta mitad. Una opción que el espejo ya tenía y que esta
    pasada todavía no llegó a ver NO puede desaparecer porque la pasada se
    cortó. Con sus etiquetas se iría la identidad PY/BR de la opción."""
    sync_attributes(
        db_session,
        attributes([[_attribute("color", [500])], [_attribute("talle", [700])]]).source(
            tenant.id
        ),
    )
    assert distinct_option_ids(db_session, tenant.id, "talle") == [700]

    # Segunda pasada, cortada antes de la página 2: la página 1 ya no nombra a
    # `talle`, así que un barrido indebido se lo llevaría.
    interrupted = attributes(
        [[_attribute("color", [500])], [_attribute("talle", [700])]], fail_before_page=1
    )
    with pytest.raises(Interrupted):
        sync_attributes(db_session, interrupted.source(tenant.id))

    assert distinct_option_ids(db_session, tenant.id, "talle") == [700]
    assert option_labels(db_session, tenant.id, "talle", 700) == {0: "opt700", 3: "opc700"}
    assert _attribute_codes(db_session, tenant.id) == ["color", "talle"]
    row = _pass_row(db_session, tenant.id, PASS_ATTRIBUTES)
    assert (row.pass_complete, row.swept) == (False, False)


def test_the_attribute_sweep_refuses_a_pass_that_did_not_finish(db_session, tenant):
    """La precondición vive DENTRO del barrido y se lee de la base, no de una
    variable del llamador: ningún camino puede omitirla. Llamarlo a mano sobre
    una pasada a medias tiene que fallar sin borrar nada."""
    sync_attributes(
        db_session,
        attributes([[_attribute("color", [500])], [_attribute("talle", [700])]]).source(
            tenant.id
        ),
    )
    interrupted = attributes(
        [[_attribute("color", [500])], [_attribute("talle", [700])]], fail_before_page=1
    )
    with pytest.raises(Interrupted):
        sync_attributes(db_session, interrupted.source(tenant.id))
    generation = _pass_row(db_session, tenant.id, PASS_ATTRIBUTES).generation

    with pytest.raises(IncompletePassSweep) as raised:
        _sweep_attributes(db_session, tenant.id, generation)

    assert "no llegó a la última página" in str(raised.value)
    assert distinct_option_ids(db_session, tenant.id, "talle") == [700]
    assert _labels_of_tenant(db_session, tenant.id) == 4


def test_the_attribute_sweep_refuses_a_generation_it_never_registered(db_session, tenant):
    """Un barrido con una generación ajena borraría TODAS las opciones y
    etiquetas del tenant: ninguna fila lleva ese sello. Se rechaza."""
    sync_attributes(db_session, attributes([[_attribute("color", [500])]]).source(tenant.id))

    with pytest.raises(IncompletePassSweep) as raised:
        _sweep_attributes(db_session, tenant.id, 999_999)

    assert "999999" in str(raised.value)
    assert distinct_option_ids(db_session, tenant.id, "color") == [500]
    assert _labels_of_tenant(db_session, tenant.id) == 2


def test_the_attribute_sweep_never_touches_another_tenants_rows(
    db_session, tenant, other_tenant
):
    """Un barrido es un DELETE sobre tablas compartidas. Las filas del otro
    tenant se siembran SIN sellar (generación 0, que es lo que tiene una fila
    preexistente): si al borrado le faltara el filtro por tenant, serían
    exactamente las que se llevaría."""
    sync_attributes(
        db_session, attributes([[_attribute("color", [500, 501])]]).source(other_tenant.id)
    )
    # Se desella a mano lo del otro tenant, para que sea candidato perfecto.
    db_session.execute(
        Attribute.__table__.update()
        .where(Attribute.tenant_id == other_tenant.id)
        .values(sync_generation=0)
    )
    db_session.execute(
        AttributeOption.__table__.update()
        .where(AttributeOption.tenant_id == other_tenant.id)
        .values(sync_generation=0)
    )
    db_session.commit()

    sync_attributes(db_session, attributes([[_attribute("color", [500])]]).source(tenant.id))

    assert _attribute_codes(db_session, other_tenant.id) == ["color"]
    assert distinct_option_ids(db_session, other_tenant.id, "color") == [500, 501]
    assert _labels_of_tenant(db_session, other_tenant.id) == 4
    # Y las etiquetas del otro tenant tampoco se fueron por el camino del
    # `option_row_id`, que es el único alcance que tiene esa tabla.
    assert _label_count(db_session) == 4 + 2


# --- categorías -------------------------------------------------------------


def test_a_category_the_origin_dropped_is_swept_with_its_store_states(db_session, tenant):
    sync_categories(
        db_session,
        categories([[_category(252, [1, 3]), _category(300, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )

    report = sync_categories(
        db_session, categories([[_category(252, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )

    assert _category_ids(db_session, tenant.id) == [252]
    assert report.categories_deleted == 1
    assert report.category_store_states_deleted == 2
    # La categoría viva conserva sus DOS estados.
    assert _store_states(db_session, tenant.id) == {(252, 1), (252, 3)}


def test_a_store_state_of_a_store_view_that_disappeared_is_swept(db_session, tenant):
    """El estado por tienda se sella aparte de la categoría a propósito: una
    store view retirada de la instancia deja la categoría viva y su estado
    huérfano, y `derive_category_effect` leería un `is_active` de una tienda
    que no existe."""
    sync_categories(
        db_session, categories([[_category(252, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )

    report = sync_categories(
        db_session, categories([[_category(252, [1])]]).source(tenant.id),
        store_view_ids=[1],
    )

    assert _category_ids(db_session, tenant.id) == [252]
    assert report.categories_deleted == 0
    assert report.category_store_states_deleted == 1
    assert _store_states(db_session, tenant.id) == {(252, 1)}


def test_an_interrupted_category_pass_does_not_sweep(db_session, tenant):
    sync_categories(
        db_session,
        categories([[_category(252, [1, 3])], [_category(300, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )

    interrupted = categories(
        [[_category(252, [1, 3])], [_category(300, [1, 3])]], fail_before_page=1
    )
    with pytest.raises(Interrupted):
        sync_categories(db_session, interrupted.source(tenant.id), store_view_ids=[1, 3])

    assert _category_ids(db_session, tenant.id) == [252, 300]
    assert _store_states(db_session, tenant.id) == {(252, 1), (252, 3), (300, 1), (300, 3)}
    row = _pass_row(db_session, tenant.id, PASS_CATEGORIES)
    assert (row.pass_complete, row.swept) == (False, False)


def test_the_category_sweep_refuses_a_pass_that_did_not_finish(db_session, tenant):
    sync_categories(
        db_session,
        categories([[_category(252, [1, 3])], [_category(300, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )
    interrupted = categories(
        [[_category(252, [1, 3])], [_category(300, [1, 3])]], fail_before_page=1
    )
    with pytest.raises(Interrupted):
        sync_categories(db_session, interrupted.source(tenant.id), store_view_ids=[1, 3])
    generation = _pass_row(db_session, tenant.id, PASS_CATEGORIES).generation

    with pytest.raises(IncompletePassSweep) as raised:
        _sweep_categories(db_session, tenant.id, generation)

    assert "no llegó a la última página" in str(raised.value)
    assert _category_ids(db_session, tenant.id) == [252, 300]


def test_the_category_sweep_refuses_a_generation_it_never_registered(db_session, tenant):
    sync_categories(
        db_session, categories([[_category(252, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )

    with pytest.raises(IncompletePassSweep):
        _sweep_categories(db_session, tenant.id, 999_999)

    assert _category_ids(db_session, tenant.id) == [252]
    assert _store_states(db_session, tenant.id) == {(252, 1), (252, 3)}


def test_the_category_sweep_never_touches_another_tenants_rows(
    db_session, tenant, other_tenant
):
    sync_categories(
        db_session, categories([[_category(252, [1, 3]), _category(999, [1])]]).source(
            other_tenant.id
        ),
        store_view_ids=[1, 3],
    )
    db_session.execute(
        Category.__table__.update()
        .where(Category.tenant_id == other_tenant.id)
        .values(sync_generation=0)
    )
    db_session.execute(
        CategoryStoreState.__table__.update()
        .where(CategoryStoreState.tenant_id == other_tenant.id)
        .values(sync_generation=0)
    )
    db_session.commit()

    sync_categories(
        db_session, categories([[_category(252, [1, 3])]]).source(tenant.id),
        store_view_ids=[1, 3],
    )

    assert _category_ids(db_session, other_tenant.id) == [252, 999]
    assert _store_states(db_session, other_tenant.id) == {(252, 1), (252, 3), (999, 1)}


def test_the_two_passes_do_not_share_a_seal_row(db_session, tenant):
    """Una fila por (tenant, tipo de pasada): si compartieran fila, terminar la
    de categorías autorizaría el barrido de atributos de una pasada a medias."""
    sync_attributes(db_session, attributes([[_attribute("color", [500])]]).source(tenant.id))
    sync_categories(
        db_session, categories([[_category(252, [1])]]).source(tenant.id), store_view_ids=[1]
    )

    kinds = sorted(
        db_session.scalars(
            select(SyncPass.pass_kind).where(SyncPass.tenant_id == tenant.id)
        ).all()
    )
    assert kinds == [PASS_ATTRIBUTES, PASS_CATEGORIES]
