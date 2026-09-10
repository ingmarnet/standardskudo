"""H3 — La reparación dirigida de una partición divergente.

H1 dejó que `reconcile()` diga QUÉ particiones de 256 divergen, y el remedio
siguió siendo `full_sync`: 228.881 productos para reparar ~900. Estas pruebas
afirman las tres cosas que hacen que el remedio use el lugar que la detección
nombra:

1. Sólo se releen los SKUs de las particiones pedidas. Si la reparación
   recorriera el catálogo, la prueba del payload lo vería.
2. Lo que la partición ya no contiene se borra del espejo — de ESA store view
   y de ninguna otra, y de ese tenant y de ningún otro.
3. Una respuesta que no trae lo que se pidió ABORTA en vez de tomar la
   ausencia por "partición vacía", que borraría del espejo la cohorte entera.
"""

from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import MIRRORED_AT, checksums_payload, skudo_response

from skudo.ingest.reconcile import partition_of, reconcile
from skudo.ingest.repair import (
    RepairReport,
    partitions_needing_repair,
    repair_partitions,
)
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, get_record, upsert_record

# Dos SKUs de particiones distintas, comprobado en la prueba de abajo: sin eso
# "reparar sólo una partición" no discriminaría nada.
SKU_A = "SKU-A"
SKU_B = "SKU-B"


def _item(sku: str, name: str) -> dict:
    return {
        "sku": sku,
        "mpn": None,
        "model": None,
        "gtin": None,
        "variant_key": None,
        "attribute_set_id": 4,
        "type_id": "simple",
        "global_values": {"name": name},
        "store_values": {},
        "website_ids": [1],
        "category_ids": [],
        # La MISMA fecha con la que `checksums_payload` arma el digest: si
        # el doble sirviera otra, el espejo reparado divergiría del digest
        # publicado y la última prueba de este archivo no significaría nada.
        "updated_at": MIRRORED_AT.strftime("%Y-%m-%d %H:%M:%S"),
    }


class FakeInstance:
    """El módulo visto desde el ingestor: qué SKUs tiene y con qué valores.

    Registra las particiones pedidas a `/checksums` y los SKUs pedidos a
    `/products-by-sku`, que es lo que permite afirmar que la reparación es
    DIRIGIDA y no un recorrido completo con otro nombre.
    """

    def __init__(self, names: dict[str, str], *, omit_partition_skus: bool = False,
                 serve_only: list[str] | None = None):
        self.names = names
        self.omit_partition_skus = omit_partition_skus
        # SKUs que `/products-by-sku` devuelve, cuando se quiere simular el
        # desacuerdo entre los dos endpoints.
        self.serve_only = serve_only
        self.requested_partitions: list[str] = []
        self.requested_skus: list[str] = []
        self.products_pages_requested = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/checksums"):
                payload = checksums_payload(sorted(self.names))
                requested = request.url.params.get("partitions")
                wanted = requested.split(",") if requested else []
                self.requested_partitions.extend(wanted)
                if not self.omit_partition_skus:
                    payload["partition_skus"] = [
                        {
                            "partition": partition,
                            "skus": sorted(
                                sku for sku in self.names if partition_of(sku) == partition
                            ),
                        }
                        for partition in wanted
                    ]
                return skudo_response(payload)
            if path.endswith("/products-by-sku"):
                asked = request.read().decode()
                import json as _json

                skus = _json.loads(asked)["skus"]
                self.requested_skus.extend(skus)
                served = [
                    sku
                    for sku in skus
                    if sku in self.names
                    and (self.serve_only is None or sku in self.serve_only)
                ]
                return skudo_response(
                    {"items": [_item(sku, self.names[sku]) for sku in served]}
                )
            if path.endswith("/products"):
                self.products_pages_requested += 1
                return skudo_response(
                    {
                        "items": [_item(sku, name) for sku, name in self.names.items()],
                        "next_cursor": None,
                    }
                )
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


def _mirror(db_session, tenant_id, sku, store_id=1, name="viejo"):
    upsert_record(
        db_session, tenant_id, store_id, ProductIdentity(sku=sku),
        {"name": name}, {"name": "global"}, None,
    )
    # Confirmado, no sólo `flush()`: la prueba del módulo sin `partition_skus`
    # hace `rollback()` para comprobar que la reparación abortada no dejó
    # nada, y con la fila del espejo sin confirmar ese rollback se la llevaría
    # también, haciendo pasar la prueba por la razón equivocada.
    db_session.commit()


def test_the_two_fixture_skus_live_in_different_partitions():
    """La fixture depende de esto; si algún día no fuera cierto, todas las
    pruebas de "sólo esa partición" pasarían por accidente."""
    assert partition_of(SKU_A) != partition_of(SKU_B)


def test_only_the_skus_of_the_named_partition_are_reread(db_session, tenant):
    instance = FakeInstance({SKU_A: "nuevo A", SKU_B: "nuevo B"})
    _mirror(db_session, tenant.id, SKU_A)
    _mirror(db_session, tenant.id, SKU_B)

    report = repair_partitions(
        db_session, instance.source(tenant.id), 1, [partition_of(SKU_A)]
    )

    assert instance.requested_skus == [SKU_A]
    assert instance.products_pages_requested == 0
    assert report.skus_reread == 1
    assert report.records_written == 1
    # El de la partición reparada se actualizó; el de la otra quedó intacto.
    assert get_record(db_session, tenant.id, SKU_A, 1).attributes["name"] == "nuevo A"
    assert get_record(db_session, tenant.id, SKU_B, 1).attributes["name"] == "viejo"


def test_a_sku_the_instance_no_longer_has_is_dropped_from_that_partition(
    db_session, tenant
):
    """La divergencia por SOBRA: el espejo tiene un SKU que la partición ya no
    contiene. Sin este borrado, la reparación no podría cerrar esa clase de
    divergencia y `reconcile` seguiría reportándola después de repararla."""
    instance = FakeInstance({SKU_A: "nuevo A"})
    _mirror(db_session, tenant.id, SKU_A)
    fantasma = next(
        sku for sku in (f"F{n}" for n in range(500)) if partition_of(sku) == partition_of(SKU_A)
    )
    _mirror(db_session, tenant.id, fantasma)

    report = repair_partitions(
        db_session, instance.source(tenant.id), 1, [partition_of(SKU_A)]
    )

    assert get_record(db_session, tenant.id, fantasma, 1) is None
    assert get_record(db_session, tenant.id, SKU_A, 1) is not None
    assert report.records_deleted == 1


def test_the_repair_does_not_touch_another_store_view(db_session, tenant):
    """El espejo guarda una fila por (producto, store view). Reparar la
    partición de PY no puede borrar la fila de BR, que esta reparación no
    leyó."""
    instance = FakeInstance({SKU_A: "nuevo A"})
    _mirror(db_session, tenant.id, SKU_A, store_id=1)
    _mirror(db_session, tenant.id, SKU_A, store_id=3)
    fantasma_br = next(
        sku for sku in (f"F{n}" for n in range(500)) if partition_of(sku) == partition_of(SKU_A)
    )
    _mirror(db_session, tenant.id, fantasma_br, store_id=3)

    repair_partitions(db_session, instance.source(tenant.id), 1, [partition_of(SKU_A)])

    assert get_record(db_session, tenant.id, fantasma_br, 3) is not None


def test_the_repair_does_not_cross_tenants(db_session):
    a = Tenant(code="a", name="A", base_url="https://a.test", token_env_var="X")
    b = Tenant(code="b", name="B", base_url="https://b.test", token_env_var="Y")
    db_session.add_all([a, b])
    db_session.flush()
    _mirror(db_session, b.id, SKU_A)
    instance = FakeInstance({})

    repair_partitions(db_session, instance.source(a.id), 1, [partition_of(SKU_A)])

    assert get_record(db_session, b.id, SKU_A, 1) is not None


def test_a_module_without_partition_skus_aborts_instead_of_emptying_the_mirror(
    db_session, tenant
):
    """El fallo peligroso: si la ausencia de `partition_skus` se leyera como
    "esta partición está vacía", la reparación borraría del espejo la cohorte
    entera. Se aborta, y el espejo queda como estaba."""
    instance = FakeInstance({SKU_A: "nuevo A"}, omit_partition_skus=True)
    _mirror(db_session, tenant.id, SKU_A)

    with pytest.raises(RuntimeError) as raised:
        repair_partitions(
            db_session, instance.source(tenant.id), 1, [partition_of(SKU_A)]
        )

    assert "partition_skus" in str(raised.value)
    db_session.rollback()
    assert get_record(db_session, tenant.id, SKU_A, 1) is not None


def test_a_sku_the_other_endpoint_does_not_return_is_reported_not_hidden(
    db_session, tenant
):
    """Los dos endpoints salen de la misma población, así que esto no debería
    pasar; si pasa, es un desacuerdo entre dos endpoints del mismo módulo y
    tiene que verse en el reporte de la pasada, no tres fases después."""
    instance = FakeInstance({SKU_A: "nuevo A"}, serve_only=[])
    _mirror(db_session, tenant.id, SKU_A)

    report = repair_partitions(
        db_session, instance.source(tenant.id), 1, [partition_of(SKU_A)]
    )

    assert report.skus_not_returned == [SKU_A]
    assert report.records_written == 0


def test_an_empty_partition_list_is_refused(db_session, tenant):
    """Reparar 'nada' en silencio dejaría creer que la divergencia se
    atendió."""
    with pytest.raises(ValueError):
        repair_partitions(db_session, FakeInstance({}).source(tenant.id), 1, [])


def test_a_malformed_partition_is_refused_before_the_request(db_session, tenant):
    instance = FakeInstance({})

    with pytest.raises(ValueError) as raised:
        repair_partitions(db_session, instance.source(tenant.id), 1, ["BASURA"])

    assert "forma inválida" in str(raised.value)
    assert instance.requested_partitions == []


def test_the_repair_reads_the_partitions_reconcile_named(db_session, tenant):
    """El remedio se alimenta de la detección: las particiones que se reparan
    son las que `reconcile` reportó, sin que nadie las copie a mano."""
    instance = FakeInstance({SKU_A: "nuevo A", SKU_B: "nuevo B"})
    # El espejo tiene los dos SKUs pero con `magento_updated_at` desconocido,
    # así que el digest de contenido de sus dos particiones diverge.
    _mirror(db_session, tenant.id, SKU_A)
    _mirror(db_session, tenant.id, SKU_B)

    drift = reconcile(db_session, instance.source(tenant.id), 1)
    partitions = partitions_needing_repair(drift)

    assert sorted(partitions) == sorted({partition_of(SKU_A), partition_of(SKU_B)})

    report = repair_partitions(db_session, instance.source(tenant.id), 1, partitions)

    assert isinstance(report, RepairReport)
    assert sorted(instance.requested_skus[-2:]) == sorted([SKU_A, SKU_B])
    after = reconcile(db_session, instance.source(tenant.id), 1)
    assert after.content_matches is True
