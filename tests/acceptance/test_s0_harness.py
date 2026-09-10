import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response

from skudo.acceptance.s0 import run_s0_acceptance
from skudo.ingest.attribute_sync import sync_attributes
from skudo.ingest.category_sync import sync_categories
from skudo.ingest.full_sync import full_sync
from skudo.ingest.reconcile import sku_digest
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, upsert_record

FIXTURES = Path(__file__).parent.parent / "fixtures"
ENVIRONMENT = json.loads((FIXTURES / "environment_opensource.json").read_text())

# Topología del tenant piloto (ver `environment_opensource.json`): store view 1
# (`py`) cuelga del grupo 1, website 1; store view 3 (`br`) cuelga del grupo 2,
# website 2. Ambos grupos comparten `root_category_id = 2`, así que el árbol
# nunca discrimina entre las dos tiendas — lo que discrimina es `is_active`
# por tienda y el website del producto, igual que en
# `tests/mirror/test_category_effect_real.py`.
PY_STORE = 1
BR_STORE = 3


def make_source(tenant_id: int, skus: list[str] | dict[int, list[str]]) -> TenantSource:
    """`skus` puede ser una lista (el mismo catálogo en toda tienda) o un mapa
    store view -> SKUs, para poder describir una tienda con población propia."""
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def skus_of(store_id: int) -> list[str]:
        return skus[store_id] if isinstance(skus, dict) else skus

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return skudo_response(environment)
        if request.url.path.endswith("/checksums"):
            store_skus = skus_of(int(request.url.params["storeId"]))
            return skudo_response({
                    "product_count": len(store_skus),
                    "sku_digest": sku_digest(store_skus),
            })
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


# Una opción de `color` con etiqueta distinta por store view: el caso que
# `identidad_de_opciones` exige encontrar como UNA sola `option_id`. Se usa
# tanto en `prepared` (vía `sync_attributes`, nunca `upsert_attribute`/
# `upsert_option` directos) como en el "healthy mirror" de más abajo.
COLOR_ATTRIBUTE_PAGE = {
    "items": [
        {
            "code": "color", "label": "Color", "frontend_input": "select",
            "declared_scope": "store", "is_filterable": True, "is_required": False,
            "attribute_set_ids": [4],
            "options": [{"option_id": 17, "labels": {"1": "Negro", "3": "Preto"}}],
        },
    ],
    "next_cursor": None,
}


def make_attribute_source(tenant_id: int, page: dict = COLOR_ATTRIBUTE_PAGE) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/attributes"):
            cursor = request.url.params.get("cursor")
            return skudo_response({"items": [], "next_cursor": None} if cursor else page)
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def prepared(db_session):
    """El fixture compartido de las pruebas de deriva/población. Los dos
    registros de producto se escriben a mano porque lo que estas pruebas
    ejercitan es la comparación de conteos contra Magento (`reconcile`), no la
    ingesta en sí — eso ya lo prueba `tests/ingest/test_full_sync.py`.

    El atributo `color`, en cambio, YA NO se siembra a mano
    (`upsert_attribute`/`upsert_option` directos): sale de `sync_attributes`
    contra una página HTTP mockeada, igual que en
    `tests/ingest/test_attribute_sync.py`. Ese sembrado a mano era exactamente
    el fixture que dejaba pasar `identidad_de_opciones` sobre un dato que el
    propio test había inventado, y no un ingestor real.
    """
    from datetime import UTC, datetime

    tenant = Tenant(code="nissei", name="Nissei", base_url="https://x.test",
                    token_env_var="T")
    db_session.add(tenant)
    db_session.flush()

    for store_id in (1, 3):
        upsert_record(db_session, tenant.id, store_id, ProductIdentity(sku="SKU1"),
                      {"name": "N"}, {"name": "store"},
                      datetime(2026, 9, 1, tzinfo=UTC))

    sync_attributes(db_session, make_attribute_source(tenant.id))
    db_session.flush()
    return tenant


def make_ingestion_source(
    tenant_id: int,
    *,
    products_page: dict,
    categories_page: dict,
    attributes_page: dict,
    checksum_skus: list[str],
) -> TenantSource:
    """A diferencia de `make_source`, este responde también `/products`,
    `/categories` y `/attributes`: sirve para poblar el espejo por los
    ingestores reales (`full_sync`, `sync_categories`, `sync_attributes`) en
    vez de escribir filas a mano, que es justo lo que este test debe dejar de
    hacer."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cursor = request.url.params.get("cursor")
        if path.endswith("/environment"):
            return skudo_response(ENVIRONMENT)
        if path.endswith("/checksums"):
            return skudo_response({
                    "product_count": len(checksum_skus),
                    "sku_digest": sku_digest(checksum_skus),
            })
        if path.endswith("/products"):
            return skudo_response(products_page)
        if path.endswith("/categories"):
            return skudo_response({"items": [], "next_cursor": None} if cursor else categories_page)
        if path.endswith("/attributes"):
            return skudo_response({"items": [], "next_cursor": None} if cursor else attributes_page)
        return httpx.Response(404)

    return TenantSource.from_tenant(
        SimpleNamespace(id=tenant_id, base_url="https://x.test"),
        token="t",
        transport=httpx.MockTransport(handler),
    )


# Categoría 252, caso real documentado en Task A3/A4: inactiva en PY, activa
# en BR. El producto está en los dos websites, así que solo `is_active` por
# tienda puede discriminar aquí — el árbol y el website no.
CATEGORY_PAGE = {
    "items": [
        {
            "category_id": 252,
            "path": [1, 2, 222, 252],
            "default_name": "Todo por menos de US$ 500",
            "store_states": [
                {"store_id": PY_STORE, "is_active": False,
                 "name": "Todo por menos de US$ 500"},
                {"store_id": BR_STORE, "is_active": True,
                 "name": "Tudo por menos de US$ 500"},
            ],
        },
    ],
    "next_cursor": None,
}

def _product_item(sku: str, *, website_ids, category_ids: list[int]) -> dict:
    return {
        "sku": sku, "mpn": None, "model": None, "gtin": None, "variant_key": None,
        "attribute_set_id": 4, "type_id": "simple",
        "global_values": {"name": sku}, "store_values": {},
        "website_ids": website_ids, "category_ids": category_ids,
        "updated_at": "2026-09-01 10:00:00",
    }


def test_all_five_criteria_pass_on_a_healthy_mirror(db_session):
    """El "healthy mirror" de punta a punta: nada se siembra a mano. Los
    atributos, opciones, categorías, topología y productos salen de páginas
    HTTP mockeadas a través de `full_sync`/`sync_attributes`/`sync_categories`,
    exactamente como en `tests/ingest/test_attribute_sync.py` y
    `tests/ingest/test_category_sync.py`. El fixture sembrado a mano que este
    test tenía antes (`upsert_attribute`/`upsert_option` directos) es
    precisamente la razón por la que C1 sobrevivió once revisiones: probaba
    el criterio contra un dato que el propio test había inventado."""
    tenant = Tenant(code="nissei", name="Nissei", base_url="https://x.test",
                    token_env_var="T")
    db_session.add(tenant)
    db_session.flush()

    products_page = {
        "items": [_product_item("SKU1", website_ids=[1, 2], category_ids=[252])],
        "next_cursor": None,
    }
    source = make_ingestion_source(
        tenant.id,
        products_page=products_page,
        categories_page=CATEGORY_PAGE,
        attributes_page=COLOR_ATTRIBUTE_PAGE,
        checksum_skus=["SKU1"],
    )

    full_sync(db_session, source, store_view_ids=[PY_STORE, BR_STORE])
    sync_attributes(db_session, source)
    sync_categories(db_session, source, store_view_ids=[PY_STORE, BR_STORE])

    results = run_s0_acceptance(db_session, source, [PY_STORE, BR_STORE])

    assert [r.name for r in results] == [
        "espejo_sincronizado", "score_por_store_view", "procedencia_de_scope",
        "identidad_de_opciones", "efecto_de_categoria",
    ]
    assert all(r.passed for r in results), [r.detail for r in results if not r.passed]


def test_efecto_de_categoria_fails_on_an_empty_mirror(db_session, prepared):
    """El espejo de `prepared` no tiene ni una categoría: el criterio debe
    reprobar, no aprobar por vacuidad. Si esto pasara, cualquier tenant sin
    categorías espejadas todavía "certificaría" el efecto de categoría."""
    results = run_s0_acceptance(db_session, make_source(prepared.id, ["SKU1"]), [1, 3])

    failed = {r.name: r.detail for r in results if not r.passed}
    assert "efecto_de_categoria" in failed
    assert "categorías" in failed["efecto_de_categoria"]


def test_efecto_de_categoria_fails_when_the_effect_is_identical_everywhere(db_session):
    """Categoría activa en las dos tiendas, producto en los dos websites: las
    tres condiciones de `derive_category_effect` dan el mismo veredicto en PY
    y en BR. El criterio debe reprobar: hay datos, pero ninguno discrimina.

    Esta es la prueba de la dirección que falla: una implementación hueca que
    solo comprobara "¿hay categorías espejadas?" pasaría aquí igual que en el
    espejo sano, sin haber demostrado jamás que `derive_category_effect`
    distingue nada."""
    tenant = Tenant(code="nissei", name="Nissei", base_url="https://x.test",
                    token_env_var="T")
    db_session.add(tenant)
    db_session.flush()

    identical_category_page = {
        "items": [
            {
                "category_id": 300,
                "path": [1, 2, 300],
                "default_name": "Ofertas",
                "store_states": [
                    {"store_id": PY_STORE, "is_active": True, "name": "Ofertas"},
                    {"store_id": BR_STORE, "is_active": True, "name": "Ofertas"},
                ],
            },
        ],
        "next_cursor": None,
    }
    products_page = {
        "items": [_product_item("SKU1", website_ids=[1, 2], category_ids=[300])],
        "next_cursor": None,
    }
    source = make_ingestion_source(
        tenant.id,
        products_page=products_page,
        categories_page=identical_category_page,
        attributes_page={"items": [], "next_cursor": None},
        checksum_skus=["SKU1"],
    )

    full_sync(db_session, source, store_view_ids=[PY_STORE, BR_STORE])
    sync_categories(db_session, source, store_view_ids=[PY_STORE, BR_STORE])

    results = run_s0_acceptance(db_session, source, [PY_STORE, BR_STORE])

    failed = {r.name: r.detail for r in results if not r.passed}
    assert "efecto_de_categoria" in failed
    assert "0 producto(s) con efecto de categoría distinto" in failed["efecto_de_categoria"]


def test_website_desconocido_is_reported_as_not_evaluated_not_as_a_failure(db_session):
    """Un producto con `website_ids=None` (Task A7.3: fila preexistente a la
    migración `0011`, o un sync que todavía no lo informó) da
    `is_effective=None` en las dos store views para su categoría. Eso NO debe
    contarse como "efecto distinto" (sería inventar una coincidencia de un
    dato ausente) ni ocultar que el resto del catálogo sí demuestra el
    criterio: SKU1 (categoría 252, discriminante) sigue haciendo pasar el
    criterio, y SKU-DESCONOCIDO se reporta aparte, como no evaluado.

    Si el criterio usara `if not effect.is_effective:` para decidir "sin
    efecto", este caso se contaría como un defecto real en vez de como un
    control pendiente — exactamente el error que A7.3 existe para prevenir."""
    tenant = Tenant(code="nissei", name="Nissei", base_url="https://x.test",
                    token_env_var="T")
    db_session.add(tenant)
    db_session.flush()

    # Categoría 300: activa en las dos tiendas, para que la ÚNICA condición
    # que decida el efecto de SKU-DESCONOCIDO sea el website, que es
    # justamente el que no se conoce.
    categories_page = {
        "items": [
            {
                "category_id": 252,
                "path": [1, 2, 222, 252],
                "default_name": "Todo por menos de US$ 500",
                "store_states": [
                    {"store_id": PY_STORE, "is_active": False,
                     "name": "Todo por menos de US$ 500"},
                    {"store_id": BR_STORE, "is_active": True,
                     "name": "Tudo por menos de US$ 500"},
                ],
            },
            {
                "category_id": 300,
                "path": [1, 2, 300],
                "default_name": "Ofertas",
                "store_states": [
                    {"store_id": PY_STORE, "is_active": True, "name": "Ofertas"},
                    {"store_id": BR_STORE, "is_active": True, "name": "Ofertas"},
                ],
            },
        ],
        "next_cursor": None,
    }
    products_page = {
        "items": [
            _product_item("SKU1", website_ids=[1, 2], category_ids=[252]),
            _product_item("SKU-DESCONOCIDO", website_ids=None, category_ids=[300]),
        ],
        "next_cursor": None,
    }
    source = make_ingestion_source(
        tenant.id,
        products_page=products_page,
        categories_page=categories_page,
        attributes_page={"items": [], "next_cursor": None},
        checksum_skus=["SKU1", "SKU-DESCONOCIDO"],
    )

    full_sync(db_session, source, store_view_ids=[PY_STORE, BR_STORE])
    sync_categories(db_session, source, store_view_ids=[PY_STORE, BR_STORE])

    results = run_s0_acceptance(db_session, source, [PY_STORE, BR_STORE])

    by_name = {r.name: r for r in results}
    effect = by_name["efecto_de_categoria"]
    assert effect.passed, effect.detail
    assert "1 producto(s) con efecto de categoría distinto" in effect.detail
    assert "1 sin evaluar por website_desconocido" in effect.detail


def test_the_four_pre_existing_criteria_still_pass_without_category_data(db_session, prepared):
    """`prepared` no sincroniza atributos ni categorías: es el fixture de las
    pruebas de deriva/población, no del "healthy mirror" (ese es
    `test_all_five_criteria_pass_on_a_healthy_mirror`, arriba, que sí ingiere
    todo por los sincronizadores reales). Aquí solo se exige que los cuatro
    criterios que ya existían antes de esta tarea sigan sanos; el quinto
    reprueba por falta de datos y eso se prueba aparte
    (`test_efecto_de_categoria_fails_on_an_empty_mirror`)."""
    results = run_s0_acceptance(
        db_session, make_source(prepared.id, ["SKU1"]), [1, 3]
    )

    by_name = {r.name: r for r in results}
    for name in ("espejo_sincronizado", "score_por_store_view",
                 "procedencia_de_scope", "identidad_de_opciones"):
        assert by_name[name].passed, by_name[name].detail


def test_drift_makes_the_first_criterion_fail(db_session, prepared):
    results = run_s0_acceptance(
        db_session, make_source(prepared.id, ["SKU1", "SKU-FANTASMA"]), [1, 3]
    )

    failed = {r.name: r for r in results if not r.passed}
    assert "espejo_sincronizado" in failed
    assert "2" in failed["espejo_sincronizado"].detail


def _add_record(db_session, tenant_id, sku, store_id):
    from datetime import UTC, datetime

    upsert_record(db_session, tenant_id, store_id, ProductIdentity(sku=sku),
                  {"name": sku}, {"name": "store"}, datetime(2026, 9, 1, tzinfo=UTC))


def test_a_product_present_in_only_one_store_view_is_not_a_failure(db_session, prepared):
    """Exigir que todas las store views tengan el MISMO número de productos
    invierte el principio rector del spec: en cuanto un tenant tiene un producto
    solo-PY —un website al que ese producto no pertenece, un caso legítimo y
    corriente—, una ausencia válida se reporta como defecto.

    Lo correcto es que cada tienda tenga su población COMPLETA, y la referencia
    de completa es el conteo de esa misma tienda en Magento.

    `efecto_de_categoria` queda fuera de esta aserción a propósito: `prepared`
    no sincroniza categorías (no es lo que este test ejercita), así que ese
    criterio reprueba correctamente por falta de datos — un fallo ortogonal al
    escenario bajo prueba, no una regresión de `score_por_store_view`.
    """
    _add_record(db_session, prepared.id, "SOLO-PY", 1)
    db_session.flush()

    results = run_s0_acceptance(
        db_session,
        make_source(prepared.id, {1: ["SKU1", "SOLO-PY"], 3: ["SKU1"]}),
        [1, 3],
    )

    failed = {
        r.name: r.detail for r in results
        if not r.passed and r.name != "efecto_de_categoria"
    }
    assert failed == {}


def test_an_empty_store_view_fails_the_criterion(db_session, prepared):
    """Una store view declarada sin ni un registro sí es un fallo, incluso si
    Magento también dice cero: el criterio del spec es que el sistema pueda dar
    un grado por store view, y sobre una tienda vacía no puede dar ninguno. Por
    eso este caso falla aunque no haya deriva."""
    results = run_s0_acceptance(
        db_session,
        make_source(prepared.id, {1: ["SKU1"], 3: ["SKU1"], 7: []}),
        [1, 3, 7],
    )

    failed = {r.name: r.detail for r in results if not r.passed}
    assert "score_por_store_view" in failed
    assert "7" in failed["score_por_store_view"]


def test_a_store_view_short_of_its_own_magento_count_fails(db_session, prepared):
    """El conteo de cada tienda se compara contra el de SU tienda en Magento."""
    results = run_s0_acceptance(
        db_session,
        make_source(prepared.id, {1: ["SKU1", "QUE-FALTA"], 3: ["SKU1"]}),
        [1, 3],
    )

    failed = {r.name: r.detail for r in results if not r.passed}
    assert "score_por_store_view" in failed
    assert "espejo=1" in failed["score_por_store_view"]
