import json
from pathlib import Path

import httpx
import pytest

from skudo.acceptance.s0 import run_s0_acceptance
from skudo.ingest.reconcile import sku_digest
from skudo.magento.client import MagentoClient
from skudo.mirror.attributes import upsert_attribute, upsert_option
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, upsert_record

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_client(skus: list[str]) -> MagentoClient:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if request.url.path.endswith("/checksums"):
            return httpx.Response(
                200, json={"product_count": len(skus), "sku_digest": sku_digest(skus)}
            )
        return httpx.Response(404)

    return MagentoClient("https://x.test", "t", transport=httpx.MockTransport(handler))


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
    results = run_s0_acceptance(db_session, make_client(["SKU1"]), prepared.id, [1, 3])

    assert [r.name for r in results] == [
        "espejo_sincronizado", "score_por_store_view",
        "procedencia_de_scope", "identidad_de_opciones",
    ]
    assert all(r.passed for r in results), [r.detail for r in results if not r.passed]


def test_drift_makes_the_first_criterion_fail(db_session, prepared):
    results = run_s0_acceptance(
        db_session, make_client(["SKU1", "SKU-FANTASMA"]), prepared.id, [1, 3]
    )

    failed = {r.name: r for r in results if not r.passed}
    assert "espejo_sincronizado" in failed
    assert "2" in failed["espejo_sincronizado"].detail
