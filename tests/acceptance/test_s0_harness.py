import json
from pathlib import Path

import httpx
import pytest

from skudo.acceptance.s0 import run_s0_acceptance
from skudo.ingest.reconcile import sku_digest
from skudo.ingest.source import TenantSource
from skudo.mirror.attributes import upsert_attribute, upsert_option
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, upsert_record

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_source(tenant_id: int, skus: list[str] | dict[int, list[str]]) -> TenantSource:
    """`skus` puede ser una lista (el mismo catálogo en toda tienda) o un mapa
    store view -> SKUs, para poder describir una tienda con población propia."""
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def skus_of(store_id: int) -> list[str]:
        return skus[store_id] if isinstance(skus, dict) else skus

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if request.url.path.endswith("/checksums"):
            store_skus = skus_of(int(request.url.params["storeId"]))
            return httpx.Response(
                200,
                json={
                    "product_count": len(store_skus),
                    "sku_digest": sku_digest(store_skus),
                },
            )
        return httpx.Response(404)

    return TenantSource(
        tenant_id=tenant_id,
        base_url="https://x.test",
        token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def prepared(db_session):
    from datetime import UTC, datetime

    tenant = Tenant(code="nissei", name="Nissei", base_url="https://x.test",
                    token_env_var="T")
    db_session.add(tenant)
    db_session.flush()

    for store_id in (1, 3):
        upsert_record(db_session, tenant.id, store_id, ProductIdentity(sku="SKU1"),
                      {"name": "N"}, {"name": "store"},
                      datetime(2026, 9, 1, tzinfo=UTC))

    upsert_attribute(db_session, tenant.id, {
        "code": "color", "label": "Color", "frontend_input": "select",
        "declared_scope": "store", "is_filterable": True, "is_required": False,
        "attribute_set_ids": [4],
    })
    upsert_option(db_session, tenant.id, "color", 17, {1: "Negro", 3: "Preto"})
    db_session.flush()
    return tenant


def test_all_four_criteria_pass_on_a_healthy_mirror(db_session, prepared):
    results = run_s0_acceptance(
        db_session, make_source(prepared.id, ["SKU1"]), [1, 3]
    )

    assert [r.name for r in results] == [
        "espejo_sincronizado", "score_por_store_view",
        "procedencia_de_scope", "identidad_de_opciones",
    ]
    assert all(r.passed for r in results), [r.detail for r in results if not r.passed]


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
    """
    _add_record(db_session, prepared.id, "SOLO-PY", 1)
    db_session.flush()

    results = run_s0_acceptance(
        db_session,
        make_source(prepared.id, {1: ["SKU1", "SOLO-PY"], 3: ["SKU1"]}),
        [1, 3],
    )

    failed = {r.name: r.detail for r in results if not r.passed}
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
