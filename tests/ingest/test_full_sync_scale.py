"""H3 — La escala que el criterio de aceptación exige.

El criterio 1 de S0 dice "espejo de 200k SKUs × 2 store views sincronizado", y
hasta acá NADA afirmaba escala: `_espejo_sincronizado` pasaba con un espejo de
ocho productos, y la única prueba de `full_sync` recorría dos páginas de un
item. Un mecanismo que funciona con dos filas y no con doscientas mil no es el
mismo mecanismo.

Estas pruebas son un PROXY: 10.000 productos sintéticos con transporte falso,
no 228.881 contra Magento. Lo que un proxy honesto puede demostrar, y lo que
estas pruebas demuestran, son las tres propiedades ESTRUCTURALES que deciden
si la escala real es posible:

1. La pasada completa termina y escribe TODO lo que el origen ofrece, con un
   solo sello de generación.
2. Confirma incrementalmente: una conexión INDEPENDIENTE ve crecer el espejo
   mientras la pasada corre. Con un único `commit()` al final —lo que hacía
   antes de H3— esa conexión no vería nada hasta el final, y un fallo a la
   tercera hora perdería el catálogo entero.
3. La memoria no crece con el catálogo: cada lote que llega a la base tiene
   como máximo el tamaño de una página. Es lo que hace que 228.881 productos
   cuesten lo mismo en RAM que 500.

Y la cuarta, que es la que el proxy demuestra mejor que ninguna medición: una
interrupción a mitad de una pasada de 20 páginas no borra el espejo, y la
reanudación continúa la MISMA generación.

Lo que un proxy NO puede decir es cuánto cuesta de verdad. Eso se midió a mano
contra la instancia de desarrollo y está en
`docs/superpowers/h3-cli-y-escala.md`, con tiempo de reloj y memoria pico.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response, upsert_record
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from skudo.ingest.full_sync import full_sync
from skudo.ingest.source import TenantSource
from skudo.mirror.models import FullSyncCheckpoint, ProductRecord, Tenant
from skudo.mirror.products import ProductIdentity, get_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

# El proxy. 10.000 productos en páginas de 500 son 20 páginas: suficientes
# para que "confirma por página" y "se reanuda desde la página 11" sean
# afirmaciones sobre un recorrido largo y no sobre dos vueltas de bucle, y
# rápido en la suite (~4 s). La escala real está medida en el informe.
SCALE_PRODUCTS = 10_000
PAGE_SIZE = 500
PAGES = SCALE_PRODUCTS // PAGE_SIZE


class Interrupted(RuntimeError):
    """Lo que se le hace estallar al transporte para cortar la pasada."""


class SyntheticCatalog:
    """Un catálogo de `total` productos servido por cursor.

    El cursor es el índice del primer producto de la página siguiente, así que
    un cursor guardado dice sin ambigüedad por dónde va la pasada — que es lo
    que permite afirmar desde dónde reanudó.
    """

    def __init__(self, total: int, fail_before_page: int | None = None, on_page=None):
        self.total = total
        self.fail_before_page = fail_before_page
        self.on_page = on_page
        self.pages_served = 0
        self.cursors: list[str | None] = []

    def _item(self, index: int) -> dict:
        return {
            "sku": f"ESCALA-{index:07d}",
            "mpn": f"MPN-{index}",
            "model": None,
            "gtin": None,
            "variant_key": None,
            "attribute_set_id": 4,
            "type_id": "simple",
            "global_values": {"name": f"Producto {index}", "price": "1000"},
            "store_values": {},
            "website_ids": [1],
            "category_ids": [7, 9],
            "updated_at": "2026-09-01 10:00:00",
        }

    def source(self, tenant_id: int) -> TenantSource:
        environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/environment"):
                return skudo_response(environment)
            if request.url.path.endswith("/attributes"):
                return skudo_response({"items": [], "next_cursor": None})
            if not request.url.path.endswith("/products"):
                return httpx.Response(404)

            cursor = request.url.params.get("cursor")
            start = 0 if cursor is None else int(cursor)
            page_number = start // PAGE_SIZE
            if self.fail_before_page is not None and page_number == self.fail_before_page:
                raise Interrupted(f"corte deliberado antes de la página {page_number}")
            if self.on_page is not None:
                self.on_page(page_number)

            self.cursors.append(cursor)
            self.pages_served += 1
            end = min(start + PAGE_SIZE, self.total)
            return skudo_response(
                {
                    "items": [self._item(index) for index in range(start, end)],
                    "next_cursor": None if end >= self.total else str(end),
                }
            )

        return TenantSource.from_tenant(
            SimpleNamespace(id=tenant_id, base_url="https://x.test"),
            token="token",
            transport=httpx.MockTransport(handler),
        )


@pytest.fixture
def scale_db(migrated_engine):
    """Sesión con commits DE VERDAD, y la base limpia al terminar.

    El `db_session` de la suite envuelve todo en una transacción con rollback,
    y con eso no se puede demostrar la propiedad central de esta tarea: que
    otra conexión ve el avance. Acá los commits son commits.
    """
    with migrated_engine.begin() as conn:
        _truncate(conn)
    with Session(migrated_engine) as session:
        tenant = Tenant(
            code="escala", name="Escala", base_url="https://x.test", token_env_var="T"
        )
        session.add(tenant)
        session.commit()
        yield session, tenant, migrated_engine
    with migrated_engine.begin() as conn:
        _truncate(conn)


def _truncate(conn) -> None:
    tables = [
        row[0]
        for row in conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        )
    ]
    if tables:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"))


def _committed_count(engine, tenant_id: int) -> int:
    """Cuántas filas ve una conexión INDEPENDIENTE. Es la pregunta entera: lo
    que otra conexión ve es, por definición, lo confirmado."""
    with engine.connect() as conn:
        return conn.execute(
            select(func.count())
            .select_from(ProductRecord)
            .where(ProductRecord.tenant_id == tenant_id)
        ).scalar_one()


def test_a_catalog_of_ten_thousand_products_syncs_completely(scale_db):
    session, tenant, engine = scale_db
    catalog = SyntheticCatalog(SCALE_PRODUCTS)

    report = full_sync(session, catalog.source(tenant.id), [1], page_size=PAGE_SIZE)

    assert report.pages_fetched == PAGES
    assert report.records_written == SCALE_PRODUCTS
    assert _committed_count(engine, tenant.id) == SCALE_PRODUCTS
    # Un solo sello: la pasada entera es una generación, y por eso el barrido
    # del final no se lleva nada de lo que ella misma escribió.
    with engine.connect() as conn:
        generations = conn.execute(
            select(func.count(func.distinct(ProductRecord.sync_generation))).where(
                ProductRecord.tenant_id == tenant.id
            )
        ).scalar_one()
    assert generations == 1
    assert report.records_deleted == 0
    # Y las categorías del conjunto completo, dos por producto.
    with engine.connect() as conn:
        assignments = conn.execute(
            text("SELECT COUNT(*) FROM product_category_assignment")
        ).scalar_one()
    assert assignments == SCALE_PRODUCTS * 2


def test_the_mirror_grows_while_the_pass_runs_and_not_only_at_the_end(scale_db):
    """LA prueba del commit por página, y la única que distingue el arreglo de
    su apariencia: se pregunta a una conexión INDEPENDIENTE cuántas filas ve
    en el momento en que la pasada pide cada página.

    Con un único `commit()` al final —el comportamiento anterior— esa
    conexión vería 0 en todas las observaciones hasta la última.
    """
    session, tenant, engine = scale_db
    observed: list[int] = []

    catalog = SyntheticCatalog(
        SCALE_PRODUCTS,
        on_page=lambda page: observed.append(_committed_count(engine, tenant.id)),
    )

    full_sync(session, catalog.source(tenant.id), [1], page_size=PAGE_SIZE)

    assert len(observed) == PAGES
    # Al pedir la primera página no hay nada; al pedir la segunda ya hay una
    # página confirmada, y así.
    assert observed[0] == 0
    assert observed[1] == PAGE_SIZE
    assert observed[-1] == (PAGES - 1) * PAGE_SIZE
    assert observed == sorted(observed)
    assert len(set(observed)) == PAGES, "cada página tiene que aportar filas nuevas"


def test_no_batch_that_reaches_the_database_is_bigger_than_a_page(scale_db, monkeypatch):
    """La memoria no puede crecer con el catálogo.

    Se afirma sobre el MECANISMO y no sobre una medición de RSS, que sería
    frágil y dependiente de la máquina: si ningún lote que llega a la base
    supera el tamaño de página, y hay tantos lotes como páginas, entonces no
    hay ningún punto del recorrido en el que el proceso tenga el catálogo
    entero en memoria. Una implementación que acumulara los items para
    escribirlos al final haría fallar esta prueba con un lote de 10.000.

    El coste real en RSS está medido a mano en el informe de H3 (plano en
    ~81 MB entre 5.000 y 50.000 productos).
    """
    import skudo.ingest.apply as apply_module

    sizes: list[int] = []
    original = apply_module.upsert_records

    def spy(session, rows):
        sizes.append(len(rows))
        return original(session, rows)

    monkeypatch.setattr(apply_module, "upsert_records", spy)

    session, tenant, _ = scale_db
    full_sync(session, SyntheticCatalog(SCALE_PRODUCTS).source(tenant.id), [1],
              page_size=PAGE_SIZE)

    assert sizes, "no se observó ni un lote: el espía no está en el camino real"
    assert max(sizes) <= PAGE_SIZE
    assert sum(sizes) == SCALE_PRODUCTS
    assert len(sizes) == PAGES


def test_an_interruption_halfway_through_twenty_pages_resumes_where_it_stopped(scale_db):
    """La escala es lo que hace que esto importe: cortar en la página 11 de 20
    y tener que empezar de cero es perder media pasada, y sobre el catálogo
    real son horas.

    Se afirma también lo que NO pasa: la fila rancia que la pasada todavía no
    había visto sigue en pie (el barrido no corre para una pasada a medias), y
    la reanudación no vuelve a pedir las diez páginas ya aplicadas.
    """
    session, tenant, engine = scale_db
    upsert_record(
        session, tenant.id, 1, ProductIdentity(sku="RANCIO-A-ESCALA"),
        {"name": "x"}, {"name": "global"}, None,
    )
    session.commit()

    cortada = SyntheticCatalog(SCALE_PRODUCTS, fail_before_page=11)
    with pytest.raises(Interrupted):
        full_sync(session, cortada.source(tenant.id), [1], page_size=PAGE_SIZE)

    # Media pasada confirmada, y el resto por hacer.
    assert _committed_count(engine, tenant.id) == 11 * PAGE_SIZE + 1
    checkpoint = session.scalar(
        select(FullSyncCheckpoint).where(FullSyncCheckpoint.tenant_id == tenant.id)
    )
    assert (checkpoint.pages_done, checkpoint.pass_complete, checkpoint.swept) == (
        11, False, False,
    )
    interrumpida = checkpoint.generation
    # El barrido no corrió: lo que la pasada no llegó a ver sigue ahí.
    assert get_record(session, tenant.id, "RANCIO-A-ESCALA", 1) is not None

    reanudada = SyntheticCatalog(SCALE_PRODUCTS)
    report = full_sync(session, reanudada.source(tenant.id), [1], page_size=PAGE_SIZE)

    assert report.resumed is True
    assert report.generation == interrumpida
    assert report.pages_fetched == PAGES - 11
    assert reanudada.cursors[0] == str(11 * PAGE_SIZE)
    # Completo, con un solo sello, y la fila rancia barrida al terminar.
    assert _committed_count(engine, tenant.id) == SCALE_PRODUCTS
    assert report.records_deleted == 1
    assert get_record(session, tenant.id, "RANCIO-A-ESCALA", 1) is None
