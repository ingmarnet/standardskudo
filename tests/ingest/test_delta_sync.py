import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import set_product_categories, skudo_response, upsert_record
from sqlalchemy import select

from skudo.ingest.delta_sync import delta_sync
from skudo.ingest.source import TenantSource
from skudo.mirror.models import SyncWatermark, Tenant
from skudo.mirror.products import ProductIdentity, get_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

CHANGES = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU9", "event": "delete",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}

REFRESHED = [
    {"sku": "SKU1", "mpn": None, "model": None, "gtin": None, "variant_key": None,
     "attribute_set_id": 4, "type_id": "simple",
     "global_values": {"name": "Notebook corregido"}, "store_values": {},
     "website_ids": [1], "category_ids": [], "updated_at": "2026-09-05 08:00:00"},
]

# Mismo SKU dos veces en una página: cubre las dos direcciones de la colisión
# para probar que gana el último evento (last-event-wins), no "delete" absoluto.
SAVE_THEN_DELETE_SAME_SKU = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU1", "event": "delete",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}

DELETE_THEN_SAVE_SAME_SKU = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "delete",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}


def make_source(tenant_id: int, changes=CHANGES, refreshed=None) -> TenantSource:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/environment"):
            return skudo_response(environment)
        if path.endswith("/deltas"):
            since = int(request.url.params.get("sinceId", 0))
            if since >= (changes["last_change_id"] or 0):
                return skudo_response({"items": [], "last_change_id": None})
            return skudo_response(changes)
        if path.endswith("/products-by-sku"):
            return skudo_response({"items": refreshed or REFRESHED})
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def seeded(db_session, tenant):
    from datetime import UTC, datetime

    for sku, name in (("SKU1", "Notebook"), ("SKU9", "A borrar")):
        upsert_record(db_session, tenant.id, 1,
                      ProductIdentity(sku=sku), {"name": name}, {"name": "global"},
                      datetime(2026, 9, 1, tzinfo=UTC))
    db_session.flush()
    return tenant


def test_saved_product_is_refreshed(db_session, seeded):
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == "Notebook corregido"


def test_deleted_product_is_removed_from_the_mirror(db_session, seeded):
    report = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU9", 1) is None
    assert report.records_deleted == 1


def test_watermark_advances_and_persists(db_session, seeded):
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    stored = db_session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == seeded.id)
    )
    assert stored == 42


def test_second_run_sees_no_changes(db_session, seeded):
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])
    second = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert second.changes_seen == 0
    assert second.watermark == 42


def test_save_then_delete_same_sku_in_one_page_ends_deleted(db_session, seeded):
    delta_sync(
        db_session, make_source(seeded.id, changes=SAVE_THEN_DELETE_SAME_SKU),
        store_view_ids=[1],
    )

    assert get_record(db_session, seeded.id, "SKU1", 1) is None


def test_delete_then_save_same_sku_in_one_page_ends_refreshed(db_session, seeded):
    delta_sync(
        db_session, make_source(seeded.id, changes=DELETE_THEN_SAVE_SAME_SKU),
        store_view_ids=[1],
    )

    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == "Notebook corregido"


def test_a_replayed_change_does_not_duplicate_records(db_session, seeded):
    from sqlalchemy import func

    from skudo.mirror.models import ProductRecord

    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])
    # Se fuerza el rebobinado del watermark para simular un reintento.
    db_session.execute(
        SyncWatermark.__table__.update()
        .where(SyncWatermark.tenant_id == seeded.id)
        .values(last_change_id=40)
    )
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == seeded.id, ProductRecord.sku == "SKU1"
        )
    )
    assert total == 1


def test_refresh_carries_the_website_ids(db_session, seeded):
    """Los registros sembrados por `seeded` no llevan `website_ids` (no se pasó
    al crearlos): si `delta_sync` no leyera la clave del payload, esta prueba
    seguiría viendo `None` después del refresh en vez de `[1]`."""
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    row = get_record(db_session, seeded.id, "SKU1", 1)
    assert row.website_ids == [1]


def test_refresh_carries_the_attribute_set_and_type(db_session, seeded):
    """Los dos campos tienen que sobrevivir también al camino incremental: si
    solo los leyera `full_sync`, un producto que cambia de set quedaría con el
    set viejo hasta la siguiente carga completa."""
    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    row = get_record(db_session, seeded.id, "SKU1", 1)
    assert row.attribute_set_id == 4
    assert row.type_id == "simple"


def test_the_watermark_records_when_we_last_synced_well(db_session, tenant):
    """`sync_watermark.updated_at` es el único registro de 'cuándo sincronizamos
    bien por última vez', justo la magnitud del criterio de SLA de delta. El
    modelo declaraba `onupdate=func.now()`, que NO se aplica a un
    `insert().on_conflict_do_update()` de Core: el valor tiene que ir en el
    `set_` del statement.

    Desigualdad estricta dentro de una sola transacción: solo la satisface
    `clock_timestamp()`. `func.now()` daría dos valores idénticos.
    """
    from skudo.ingest.delta_sync import _write_watermark

    def updated_at():
        return db_session.scalar(
            select(SyncWatermark.updated_at).where(SyncWatermark.tenant_id == tenant.id)
        )

    _write_watermark(db_session, tenant.id, 10)
    first = updated_at()

    _write_watermark(db_session, tenant.id, 20)

    assert updated_at() > first


BAD_DATE_REFRESHED = [
    {**REFRESHED[0], "updated_at": "0000-00-00 00:00:00"},
]


REFRESHED_ONE_FEWER_CATEGORY = [
    {**REFRESHED[0], "category_ids": [10]},
]

REFRESHED_NO_CATEGORIES = [
    {**REFRESHED[0], "category_ids": []},
]


def test_a_delta_refresh_revokes_a_category_the_product_left(db_session, seeded):
    """Caso de aceptación de A5: un producto refrescado por delta con una
    categoría menos queda con una categoría menos en el espejo. Empezar con
    UNA sola categoría no discriminaría un `set_product_categories` que solo
    diera de alta sin revocar (la 10 se insertaría igual); empezar con dos y
    terminar con una sola prueba que la 15 se revocó de verdad."""
    from sqlalchemy import select

    from skudo.mirror.models import ProductCategoryAssignment

    set_product_categories(db_session, seeded.id, "SKU1", [10, 15])
    db_session.commit()

    delta_sync(
        db_session, make_source(seeded.id, refreshed=REFRESHED_ONE_FEWER_CATEGORY),
        store_view_ids=[1],
    )

    assert db_session.scalars(
        select(ProductCategoryAssignment.category_magento_id).where(
            ProductCategoryAssignment.tenant_id == seeded.id,
            ProductCategoryAssignment.sku == "SKU1",
        )
    ).all() == [10]


def test_a_delta_refresh_with_no_categories_clears_all_assignments(db_session, seeded):
    """Una lista vacía es un estado legítimo ("sin categorías"), no "sin
    información": debe dejar cero asignaciones, no dejarlas intactas. Empezar
    con asignaciones no vacías es lo que discrimina esto de una implementación
    que trata la lista vacía como "no tocar nada"."""
    from sqlalchemy import select

    from skudo.mirror.models import ProductCategoryAssignment

    set_product_categories(db_session, seeded.id, "SKU1", [10, 15])
    db_session.commit()

    delta_sync(
        db_session, make_source(seeded.id, refreshed=REFRESHED_NO_CATEGORIES),
        store_view_ids=[1],
    )

    assert db_session.scalars(
        select(ProductCategoryAssignment.category_magento_id).where(
            ProductCategoryAssignment.tenant_id == seeded.id,
            ProductCategoryAssignment.sku == "SKU1",
        )
    ).all() == []


def test_category_replacement_runs_once_per_sku_not_once_per_store_view(
    db_session, seeded, monkeypatch
):
    """La asignación producto-categoría es global (sin store_id en
    `catalog_category_product`), así que aplicarla una vez por store view
    repetiría el mismo reemplazo de conjunto N veces. Con dos store views y un
    solo SKU a refrescar, la llamada debe ocurrir una sola vez: si el guard se
    borrara, esta prueba vería 2 llamadas en vez de 1.

    El espía se pone sobre `skudo.ingest.apply`, que es donde vive el bucle de
    escritura desde H3 (lo comparten `full_sync`, `delta_sync` y la reparación
    dirigida); el `categorized` que hace pasar esta prueba lo sigue pasando
    `delta_sync`. Desde H3 la escritura es por LOTE, así que lo que se afirma
    es el conjunto de SKUs que llega en el lote: con dos store views y un solo
    SKU a refrescar, el SKU tiene que aparecer en UN lote y no en dos."""
    import skudo.ingest.apply as apply_module

    lotes: list[list[str]] = []
    original = apply_module.set_products_categories

    def spy(session, tenant_id, assignments):
        lotes.append(sorted(assignments))
        return original(session, tenant_id, assignments)

    monkeypatch.setattr(apply_module, "set_products_categories", spy)

    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1, 3])

    assert [lote for lote in lotes if lote] == [["SKU1"]]


def test_a_poison_pill_date_does_not_block_the_watermark(db_session, seeded):
    """Este es el caso grave: si una fecha ilegible aborta la página, el
    watermark no avanza y NINGUNA sincronización posterior puede progresar. El
    cambio se aplica con la fecha desconocida y el watermark avanza."""
    report = delta_sync(
        db_session, make_source(seeded.id, refreshed=BAD_DATE_REFRESHED),
        store_view_ids=[1],
    )

    row = get_record(db_session, seeded.id, "SKU1", 1)
    assert row.attributes["name"] == "Notebook corregido"
    assert row.magento_updated_at is None
    assert report.watermark == 42
    assert report.records_without_timestamp == 1
    assert report.skus_without_timestamp == ["SKU1"]


# --- C2: la "última lectura de deltas" persistida ---------------------------
#
# Nada enviaba `sinceTimestamp`, así que todo el mecanismo de activaciones
# programadas del módulo era inalcanzable y el fallo que su propio docblock
# describe estaba vivo: con Staging activo y 181 productos multiversión, una
# actualización programada entra en vigor y el espejo sirve el valor viejo
# indefinidamente. Estas pruebas cablean el parámetro de punta a punta.

ACTIVATION_ONLY_PAGE = {
    "items": [
        {"change_id": 0, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:00:00"},
    ],
    "last_change_id": None,
}


def make_timestamp_capturing_source(tenant_id: int, deltas_page: dict):
    """Fuente que además REGISTRA el `sinceTimestamp` de cada petición."""
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())
    sent: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/environment"):
            return skudo_response(environment)
        if path.endswith("/deltas"):
            sent.append(request.url.params.get("sinceTimestamp"))
            since = int(request.url.params.get("sinceId", 0))
            if since >= (deltas_page["last_change_id"] or 0) and deltas_page["items"] \
                    and deltas_page["last_change_id"] is not None:
                return skudo_response({"items": [], "last_change_id": None})
            return skudo_response(deltas_page)
        if path.endswith("/products-by-sku"):
            return skudo_response({"items": REFRESHED})
        return httpx.Response(404)

    source = TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )
    return source, sent


def _last_delta_read_at(db_session, tenant_id):
    return db_session.scalar(
        select(SyncWatermark.last_delta_read_at).where(
            SyncWatermark.tenant_id == tenant_id
        )
    )


def test_the_first_run_sends_no_timestamp_and_records_the_moment_it_read(
    db_session, seeded
):
    """Sin lectura previa no hay ventana: `created_in > 0` capturaría la
    versión activa de TODO el catálogo como recién activada. La primera pasada
    solo deja anotado el instante desde el que la siguiente puede preguntar."""
    source, sent = make_timestamp_capturing_source(seeded.id, CHANGES)

    delta_sync(db_session, source, store_view_ids=[1])

    assert sent[0] is None
    assert _last_delta_read_at(db_session, seeded.id) is not None


def test_the_second_run_sends_the_recorded_read_minus_the_overlap(db_session, seeded):
    """La segunda pasada sí pregunta por la ventana, y lo hace con un margen de
    solape hacia atrás: el `created_in` de Magento lo pone el reloj de SU base,
    no el nuestro, y una deriva de relojes en el sentido malo se traga una
    activación para siempre. Con el solape, la deriva causa una reentrega
    (inofensiva: todo el camino es upsert) en vez de una pérdida.

    Discrimina sobre el VALOR: un `sinceTimestamp` igual al instante de lectura
    —sin margen— haría fallar la desigualdad de abajo."""
    from skudo.ingest.delta_sync import DELTA_ACTIVATION_OVERLAP_SECONDS

    first, _ = make_timestamp_capturing_source(seeded.id, CHANGES)
    delta_sync(db_session, first, store_view_ids=[1])
    read_at = _last_delta_read_at(db_session, seeded.id)

    second, sent = make_timestamp_capturing_source(seeded.id, CHANGES)
    delta_sync(db_session, second, store_view_ids=[1])

    assert sent[0] is not None
    assert int(sent[0]) == int(read_at.timestamp()) - DELTA_ACTIVATION_OVERLAP_SECONDS


def test_a_version_activation_refreshes_the_sku_without_moving_the_change_id(
    db_session, seeded
):
    """El caso que las dos mitades del contrato se contradecían sobre: una
    página cuyos únicos items son activaciones de versión (`change_id: 0`,
    `last_change_id: null`). Se aplica —el SKU se refresca de verdad— y el
    watermark de change_id NO se mueve, porque esas filas no son de la cola.

    Si el watermark tomara el `last_change_id` nulo, la columna quedaría en
    NULL/0 y la próxima pasada reprocesaría la cola entera."""
    db_session.execute(
        SyncWatermark.__table__.insert().values(
            tenant_id=seeded.id, last_change_id=99,
            last_delta_read_at=datetime(2026, 9, 5, tzinfo=UTC),
        )
    )
    db_session.flush()
    source, sent = make_timestamp_capturing_source(seeded.id, ACTIVATION_ONLY_PAGE)

    report = delta_sync(db_session, source, store_view_ids=[1])

    assert sent[0] is not None, "la activación solo llega si se envía sinceTimestamp"
    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == (
        "Notebook corregido"
    )
    assert report.watermark == 99
    assert db_session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == seeded.id)
    ) == 99


# --- M3: el borrado de un SKU se lleva sus asignaciones de categoría ---------
#
# `delta_sync` borraba el `ProductRecord` y dejaba las filas de
# `product_category_assignment` en pie. Los detectores del eje 2 de S1 leen esa
# tabla, así que la fila rancia se convierte en un hallazgo sobre un producto
# que ya no existe.


def _assignment_pairs(db_session, tenant_id: int) -> set[tuple[str, int]]:
    from skudo.mirror.models import ProductCategoryAssignment

    return set(
        db_session.execute(
            select(
                ProductCategoryAssignment.sku,
                ProductCategoryAssignment.category_magento_id,
            ).where(ProductCategoryAssignment.tenant_id == tenant_id)
        ).all()
    )


def test_a_deleted_products_category_assignments_go_with_it(db_session, seeded):
    """En la MISMA transacción que el borrado del producto, no en un barrido
    posterior: entre las dos cosas, cualquier lectura del espejo vería un
    producto sin registro pero con categorías."""
    set_product_categories(db_session, seeded.id, "SKU9", [15, 22])
    db_session.flush()

    report = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert report.records_deleted == 1
    assert report.category_assignments_deleted == 2
    assert _assignment_pairs(db_session, seeded.id) == set()


def test_deleting_a_product_does_not_touch_another_products_assignments(
    db_session, seeded
):
    """El borrado va por lista explícita de SKUs. Si fuera más amplio —o si le
    faltara el filtro por sku— se llevaría las categorías de productos vivos, y
    el detector "sin categoría" de S1 los reportaría en masa."""
    set_product_categories(db_session, seeded.id, "SKU9", [15])
    set_product_categories(db_session, seeded.id, "OTRO-VIVO", [22, 33])
    db_session.flush()

    delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert _assignment_pairs(db_session, seeded.id) == {("OTRO-VIVO", 22), ("OTRO-VIVO", 33)}


def test_a_delete_in_one_tenant_leaves_the_other_tenants_assignments_alone(
    db_session, seeded
):
    """El mismo sku borrado en un tenant no puede borrar el del otro: un
    `DELETE` sin filtro de tenant es la peor falla posible de este espejo."""
    other = Tenant(code="otro", name="Otro", base_url="https://y.test", token_env_var="T2")
    db_session.add(other)
    db_session.flush()
    upsert_record(db_session, other.id, 1, ProductIdentity(sku="SKU9"),
                  {"name": "Vivo en B"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))
    set_product_categories(db_session, other.id, "SKU9", [15, 22])
    set_product_categories(db_session, seeded.id, "SKU9", [15])
    db_session.flush()

    report = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert report.category_assignments_deleted == 1
    assert _assignment_pairs(db_session, other.id) == {("SKU9", 15), ("SKU9", 22)}
    assert get_record(db_session, other.id, "SKU9", 1) is not None


def test_a_deleted_products_signals_go_with_it(db_session, seeded):
    """Misma transacción que su registro y sus categorías: S1 prioriza por
    señal, así que un fantasma con facturación encabezaría la lista."""
    from skudo.mirror.signals import get_signal, upsert_signals

    upsert_signals(db_session, seeded.id, 1, [{"sku": "SKU9", "units_sold": 9,
                                               "uses_msi": False}])
    upsert_signals(db_session, seeded.id, 1, [{"sku": "SKU1", "units_sold": 1,
                                               "uses_msi": False}])
    db_session.flush()

    report = delta_sync(db_session, make_source(seeded.id), store_view_ids=[1])

    assert report.signals_deleted == 1
    assert get_signal(db_session, seeded.id, "SKU9", 1) is None
    # La del producto vivo sigue ahí: el borrado va por lista explícita.
    assert get_signal(db_session, seeded.id, "SKU1", 1) is not None
