"""H3 — La pasada completa se confirma por página y se reanuda.

El defecto: `full_sync` hacía UN `commit()` al final. Para el catálogo piloto
son ~457.000 upserts más un par delete+insert de categorías por producto en
una sola transacción de Postgres, sin lotes, sin commit por página y sin
reanudación: un fallo a la tercera hora lo perdía todo.

El modo de fallo que el arreglo INTRODUCE, y que estas pruebas vigilan: con
commit por página, una pasada interrumpida deja el espejo parcialmente
actualizado —correcto— pero el BARRIDO deja de ser correcto. Barrer "lo que
esta pasada no selló" sobre una pasada que vio tres páginas de cuatrocientas
borraría casi todo el catálogo. Dos pruebas de acá fallan si esa precondición
se relaja, y una tercera exige que la reanudación continúe la MISMA
generación: con una generación nueva, el barrido del final se llevaría por
delante todo lo que la pasada interrumpida ya había escrito.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response, upsert_record

from skudo.ingest.full_sync import IncompletePassSweep, _sweep, full_sync
from skudo.ingest.source import TenantSource
from skudo.mirror.models import FullSyncCheckpoint, ProductRecord, Tenant
from skudo.mirror.products import ProductIdentity, get_record

FIXTURES = Path(__file__).parent.parent / "fixtures"


class Interrupted(RuntimeError):
    """Lo que se le hace estallar al transporte para cortar una pasada."""


def _item(sku: str) -> dict:
    return {
        "sku": sku,
        "mpn": None,
        "model": None,
        "gtin": None,
        "variant_key": None,
        "attribute_set_id": 4,
        "type_id": "simple",
        "global_values": {"name": sku},
        "store_values": {},
        "website_ids": [1],
        "category_ids": [],
        "updated_at": "2026-09-01 10:00:00",
    }


class FakeCatalog:
    """Catálogo paginado por cursor, con cuenta de peticiones y corte a demanda.

    Sirve `pages` en orden; el cursor de la página `i` es `"p{i}"`, así que un
    cursor guardado en el checkpoint identifica sin ambigüedad desde dónde
    pidió la reanudación — que es justo lo que hay que poder afirmar.
    """

    def __init__(
        self,
        pages: list[list[str]],
        fail_before_page: int | None = None,
        fail_before_request: int | None = None,
    ):
        self.pages = pages
        # Corte por PÁGINA del recorrido (la página `n` de cualquier store
        # view) o por número de PETICIÓN (la n-ésima de la pasada entera, que
        # es como se corta a mitad de la SEGUNDA store view).
        self.fail_before_page = fail_before_page
        self.fail_before_request = fail_before_request
        self.requested_cursors: list[str | None] = []

    def transport(self) -> httpx.MockTransport:
        environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/environment"):
                return skudo_response(environment)
            if request.url.path.endswith("/products"):
                cursor = request.url.params.get("cursor")
                index = 0 if cursor is None else int(cursor[1:])
                if self.fail_before_request == len(self.requested_cursors):
                    raise Interrupted(
                        f"corte deliberado antes de la petición {self.fail_before_request}"
                    )
                self.requested_cursors.append(cursor)
                if self.fail_before_page is not None and index == self.fail_before_page:
                    raise Interrupted(f"corte deliberado antes de la página {index}")
                is_last = index == len(self.pages) - 1
                return skudo_response(
                    {
                        "items": [_item(sku) for sku in self.pages[index]],
                        "next_cursor": None if is_last else f"p{index + 1}",
                    }
                )
            if request.url.path.endswith("/attributes"):
                return skudo_response({"items": [], "next_cursor": None})
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def source(self, tenant_id: int) -> TenantSource:
        return TenantSource.from_tenant(
            SimpleNamespace(id=tenant_id, base_url="https://x.test"),
            token="token",
            transport=self.transport(),
        )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def _checkpoint(db_session, tenant_id, store_id) -> FullSyncCheckpoint:
    from sqlalchemy import select

    return db_session.scalar(
        select(FullSyncCheckpoint).where(
            FullSyncCheckpoint.tenant_id == tenant_id,
            FullSyncCheckpoint.store_view_magento_id == store_id,
        )
    )


def _generations(db_session, tenant_id, store_id) -> dict[str, int]:
    from sqlalchemy import select

    return {
        sku: generation
        for sku, generation in db_session.execute(
            select(ProductRecord.sku, ProductRecord.sync_generation).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_id,
            )
        ).all()
    }


def test_a_finished_pass_leaves_a_sealed_checkpoint(db_session, tenant):
    catalog = FakeCatalog([["A", "B"], ["C"]])

    report = full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])

    checkpoint = _checkpoint(db_session, tenant.id, 1)
    assert (checkpoint.pass_complete, checkpoint.swept) == (True, True)
    assert checkpoint.pages_done == 2
    assert checkpoint.next_cursor is None
    assert checkpoint.generation == report.generation
    assert report.store_views_completed == [1]
    assert report.resumed is False


def test_an_interrupted_pass_keeps_what_it_wrote_and_records_where_it_stopped(
    db_session, tenant
):
    """La mitad buena del commit por página: lo aplicado no se pierde, y el
    cursor con el que seguir queda escrito en la misma transacción."""
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)

    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])

    assert get_record(db_session, tenant.id, "A", 1) is not None
    checkpoint = _checkpoint(db_session, tenant.id, 1)
    assert checkpoint.pages_done == 1
    assert checkpoint.next_cursor == "p1"
    assert checkpoint.pass_complete is False


def test_an_interrupted_pass_does_not_sweep(db_session, tenant):
    """LA prueba. Una fila que el espejo ya tenía y que esta pasada todavía no
    llegó a ver NO puede desaparecer porque la pasada se cortó: el barrido no
    corre para una store view cuya pasada no terminó.

    Si el barrido corriera con la pasada a medias, sobre el catálogo real
    borraría todo lo que quedaba por recorrer.
    """
    upsert_record(
        db_session, tenant.id, 1, ProductIdentity(sku="TODAVIA_NO_VISTO"),
        {"name": "x"}, {"name": "global"}, None,
    )
    db_session.commit()
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)

    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])

    assert get_record(db_session, tenant.id, "TODAVIA_NO_VISTO", 1) is not None
    assert _checkpoint(db_session, tenant.id, 1).swept is False


def test_the_sweep_refuses_a_pass_that_did_not_finish(db_session, tenant):
    """La precondición vive DENTRO de `_sweep` y se lee de la base, no de una
    variable del llamador: ningún camino puede omitirla. Llamarlo a mano sobre
    una pasada a medias tiene que fallar y no borrar nada."""
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)
    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])
    generation = _checkpoint(db_session, tenant.id, 1).generation

    with pytest.raises(IncompletePassSweep) as raised:
        _sweep(db_session, tenant.id, 1, generation)

    assert "NO llegó a la última página" in str(raised.value)
    assert get_record(db_session, tenant.id, "A", 1) is not None


def test_the_sweep_refuses_a_generation_it_never_registered(db_session, tenant):
    """Un barrido con una generación que no es la del checkpoint borraría
    TODAS las filas de esa store view (ninguna lleva ese sello). Se rechaza."""
    full_sync(db_session, FakeCatalog([["A"]]).source(tenant.id), store_view_ids=[1])

    with pytest.raises(IncompletePassSweep) as raised:
        _sweep(db_session, tenant.id, 1, 999_999)

    assert "no hay checkpoint de la generación 999999" in str(raised.value)
    assert get_record(db_session, tenant.id, "A", 1) is not None


def test_a_resumed_pass_continues_the_same_generation_so_the_sweep_spares_page_one(
    db_session, tenant
):
    """La prueba que discrimina la reanudación CORRECTA de la que parece
    funcionar: si la reanudación tomara una generación nueva, las filas de la
    página 1 —escritas por la pasada interrumpida y no releídas— quedarían con
    el sello viejo y el barrido del final las borraría. El espejo terminaría
    con la última página solamente.
    """
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)
    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])
    interrupted_generation = _checkpoint(db_session, tenant.id, 1).generation

    resumed = FakeCatalog([["A", "B"], ["C"]])
    report = full_sync(db_session, resumed.source(tenant.id), store_view_ids=[1])

    assert report.resumed is True
    assert report.generation == interrupted_generation
    # La reanudación pidió la página 2 y NO volvió a pedir la 1.
    assert resumed.requested_cursors == ["p1"]
    # Y el barrido, que sí corrió, dejó las tres filas.
    assert sorted(_generations(db_session, tenant.id, 1)) == ["A", "B", "C"]
    assert set(_generations(db_session, tenant.id, 1).values()) == {
        interrupted_generation
    }
    assert report.records_deleted == 0


def test_a_resumed_pass_still_sweeps_what_the_origin_dropped(db_session, tenant):
    """Reanudar no puede volverse una excusa para no barrer nunca: la pasada
    termina, el sello queda, y lo que el origen ya no ofrece se va."""
    upsert_record(
        db_session, tenant.id, 1, ProductIdentity(sku="RANCIO"),
        {"name": "x"}, {"name": "global"}, None,
    )
    db_session.commit()
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)
    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])

    report = full_sync(
        db_session, FakeCatalog([["A", "B"], ["C"]]).source(tenant.id), store_view_ids=[1]
    )

    assert get_record(db_session, tenant.id, "RANCIO", 1) is None
    assert report.records_deleted == 1


def test_restart_takes_a_new_generation_and_walks_from_the_beginning(db_session, tenant):
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)
    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])
    interrupted_generation = _checkpoint(db_session, tenant.id, 1).generation

    restarted = FakeCatalog([["A", "B"], ["C"]])
    report = full_sync(
        db_session, restarted.source(tenant.id), store_view_ids=[1], restart=True
    )

    assert report.resumed is False
    assert report.generation > interrupted_generation
    assert restarted.requested_cursors == [None, "p1"]
    assert sorted(_generations(db_session, tenant.id, 1)) == ["A", "B", "C"]


def test_a_store_view_finished_in_this_generation_is_not_walked_twice(db_session, tenant):
    """Una pasada de dos tiendas interrumpida en la segunda no vuelve a
    recorrer la primera: ya terminó y ya fue barrida con este mismo sello, y
    releerla sería el catálogo entero para reescribir lo mismo."""
    # Un catálogo de UNA página, y el corte en la segunda PETICIÓN: la store
    # view 1 termina y se barre, la 3 no llega a empezar.
    catalog = FakeCatalog([["A"]], fail_before_request=1)
    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1, 3])
    assert _checkpoint(db_session, tenant.id, 1).swept is True
    # La 3 dejó su checkpoint abierto (cero páginas): es lo que hace que la
    # reanudación reconozca la generación en curso en vez de tomar una nueva.
    abierto = _checkpoint(db_session, tenant.id, 3)
    assert (abierto.pages_done, abierto.pass_complete) == (0, False)

    resumed = FakeCatalog([["A"]])
    report = full_sync(db_session, resumed.source(tenant.id), store_view_ids=[1, 3])

    assert report.resumed is True
    assert report.store_views_already_done == [1]
    assert report.store_views_completed == [3]
    # La store view 1 no se volvió a pedir: la única petición es la de la 3.
    assert resumed.requested_cursors == [None]


def test_the_page_size_travels_to_the_module(db_session, tenant):
    """El tamaño de página es el tamaño de la transacción: tiene que ser
    ajustable de punta a punta, no una constante escondida."""
    seen: list[str | None] = []

    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return skudo_response(environment)
        if request.url.path.endswith("/products"):
            seen.append(request.url.params.get("limit"))
            return skudo_response({"items": [_item("A")], "next_cursor": None})
        if request.url.path.endswith("/attributes"):
            return skudo_response({"items": [], "next_cursor": None})
        return httpx.Response(404)

    source = TenantSource.from_tenant(
        SimpleNamespace(id=tenant.id, base_url="https://x.test"),
        token="token",
        transport=httpx.MockTransport(handler),
    )

    full_sync(db_session, source, store_view_ids=[1], page_size=17)

    assert seen == ["17"]


def test_an_interrupted_pass_does_not_drop_category_assignments_either(
    db_session, tenant
):
    """M3 con la misma exigencia que H3 le puso al barrido de productos: una
    pasada cortada no puede llevarse las asignaciones de lo que todavía no
    llegó a ver. La limpieza referencial corre DESPUÉS del recorrido completo,
    así que una interrupción la deja sin ejecutar —el fallo benigno.
    """
    from skudo_testing import set_product_categories
    from sqlalchemy import func, select

    from skudo.mirror.models import ProductCategoryAssignment

    upsert_record(
        db_session, tenant.id, 1, ProductIdentity(sku="TODAVIA_NO_VISTO"),
        {"name": "x"}, {"name": "global"}, None,
    )
    set_product_categories(db_session, tenant.id, "TODAVIA_NO_VISTO", [15, 22])
    db_session.commit()
    catalog = FakeCatalog([["A", "B"], ["C"]], fail_before_page=1)

    with pytest.raises(Interrupted):
        full_sync(db_session, catalog.source(tenant.id), store_view_ids=[1])

    assert db_session.scalar(
        select(func.count()).select_from(ProductCategoryAssignment).where(
            ProductCategoryAssignment.tenant_id == tenant.id,
            ProductCategoryAssignment.sku == "TODAVIA_NO_VISTO",
        )
    ) == 2
