from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response, upsert_record

from skudo.ingest.reconcile import (
    PARTITION_COUNT,
    content_partitions,
    partition_of,
    reconcile,
    sku_digest,
    timestamp_token,
)
from skudo.ingest.source import TenantSource
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity

MIRRORED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def partitions_payload(rows: list[tuple[str, datetime | None]]) -> list[dict]:
    """La forma con la que `/checksums` publica las particiones: una LISTA de
    objetos ordenada por partición, sólo las no vacías. Se construye con el
    mismo algoritmo a propósito: lo que se prueba acá es la COMPARACIÓN; que el
    algoritmo coincida con el de PHP lo fija `test_the_partition_digest_matches_the_php_algorithm`
    contra valores calculados del otro lado."""
    return [
        {"partition": partition, "product_count": count, "content_digest": digest}
        for partition, (count, digest) in sorted(content_partitions(rows).items())
    ]


def make_source(
    tenant_id: int,
    count: int,
    digest: str,
    partitions: list[dict] | None = None,
    partition_count: int | None = PARTITION_COUNT,
    omit_partitions: bool = False,
) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/checksums"):
            payload: dict = {"product_count": count, "sku_digest": digest}
            if not omit_partitions:
                payload["partition_count"] = partition_count
                payload["content_partitions"] = partitions or []
            return skudo_response(payload)
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
def mirrored(db_session, tenant):
    for sku in ("SKU1", "SKU2"):
        upsert_record(db_session, tenant.id, 1, ProductIdentity(sku=sku),
                      {"name": sku}, {"name": "global"}, MIRRORED_AT)
    db_session.flush()
    return tenant


MIRRORED_ROWS = [("SKU1", MIRRORED_AT), ("SKU2", MIRRORED_AT)]


def test_digest_is_order_independent():
    assert sku_digest(["SKU2", "SKU1"]) == sku_digest(["SKU1", "SKU2"])


def test_digest_changes_when_a_sku_is_missing():
    assert sku_digest(["SKU1", "SKU2"]) != sku_digest(["SKU1"])


def test_no_drift_when_count_and_digest_match(db_session, mirrored):
    report = reconcile(
        db_session,
        make_source(
            mirrored.id, 2, sku_digest(["SKU1", "SKU2"]), partitions_payload(MIRRORED_ROWS)
        ),
        1,
    )

    assert report.digest_matches is True
    assert report.needs_full_sync is False
    assert report.content_matches is True
    assert report.diverging_partitions == []


def test_drift_detected_when_magento_has_more_products(db_session, mirrored):
    report = reconcile(
        db_session, make_source(mirrored.id, 3, "cualquier-otro-digest"), 1
    )

    assert report.magento_count == 3
    assert report.mirror_count == 2
    assert report.needs_full_sync is True


def test_drift_detected_when_counts_match_but_the_sku_set_differs(db_session, mirrored):
    """El conteo puede coincidir y el conjunto no: un SKU borrado y otro creado
    en el mismo intervalo. Por eso hace falta el digest y no solo contar."""
    report = reconcile(
        db_session, make_source(mirrored.id, 2, sku_digest(["SKU1", "SKU3"])), 1
    )

    assert report.magento_count == report.mirror_count
    assert report.digest_matches is False
    assert report.needs_full_sync is True


# --------------------------------------------------------------------------
# H1: el digest de CONTENIDO. El punto ciego que estas pruebas cierran es que
# un VALOR cambiado en un SKU existente no movía nada: ni el conteo, ni la
# huella del conjunto. El espejo podía sostener un valor rancio para siempre.
# --------------------------------------------------------------------------


def test_a_changed_value_is_detected_though_the_sku_set_is_identical(db_session, mirrored):
    """LA prueba de H1. Magento reporta el MISMO conjunto de SKUs y el MISMO
    conteo, con un `updated_at` distinto en uno de ellos. Antes de H1 esto era
    'sin deriva'."""
    otro_instante = [("SKU1", MIRRORED_AT), ("SKU2", MIRRORED_AT + timedelta(days=3))]

    report = reconcile(
        db_session,
        make_source(
            mirrored.id, 2, sku_digest(["SKU1", "SKU2"]), partitions_payload(otro_instante)
        ),
        1,
    )

    assert report.digest_matches is True, "el conjunto de SKUs no cambió: eso es el punto"
    assert report.magento_count == report.mirror_count
    assert report.content_matches is False
    assert [d.partition for d in report.diverging_partitions] == [partition_of("SKU2")]


def test_only_the_partition_of_the_changed_sku_diverges(db_session, tenant):
    """Lo que hace útil el particionado: el remedio es dirigido. Con dos SKUs
    en particiones distintas y uno cambiado, se reporta UNA partición, no
    'el catálogo'."""
    for sku in ("SKU1", "SKU2", "SKU3"):
        upsert_record(db_session, tenant.id, 1, ProductIdentity(sku=sku),
                      {}, {}, MIRRORED_AT)
    db_session.flush()

    remoto = [("SKU1", MIRRORED_AT), ("SKU2", MIRRORED_AT), ("SKU3", MIRRORED_AT + timedelta(hours=1))]
    report = reconcile(
        db_session,
        make_source(tenant.id, 3, sku_digest(["SKU1", "SKU2", "SKU3"]), partitions_payload(remoto)),
        1,
    )

    assert len(report.diverging_partitions) == 1
    drift = report.diverging_partitions[0]
    assert drift.partition == partition_of("SKU3")
    # El conteo de la partición coincide y el digest no: es un valor distinto,
    # no un producto de más. Un detector que sólo mirara conteos no lo vería.
    assert drift.magento_count == drift.mirror_count == 1
    assert drift.magento_digest != drift.mirror_digest
    assert report.partitions_compared == 3


def test_a_content_divergence_does_not_force_a_full_sync(db_session, mirrored):
    """El particionado existe para que el remedio NO sea 're-sincronizá todo'.
    `needs_full_sync` sigue reservado a las discrepancias de conjunto."""
    otro = [("SKU1", MIRRORED_AT), ("SKU2", MIRRORED_AT + timedelta(days=3))]

    report = reconcile(
        db_session,
        make_source(mirrored.id, 2, sku_digest(["SKU1", "SKU2"]), partitions_payload(otro)),
        1,
    )

    assert report.content_matches is False
    assert report.needs_full_sync is False


def test_a_partition_that_exists_on_one_side_only_is_reported(db_session, mirrored):
    """El módulo sólo emite particiones no vacías, así que un SKU que existe
    de un solo lado aparece como partición de un solo lado. Se reporta, con
    `None` en el lado que no la tiene, en vez de omitirse por no poder
    compararse."""
    remoto = [("SKU1", MIRRORED_AT), ("SKU2", MIRRORED_AT), ("SKU-EXTRA", MIRRORED_AT)]

    report = reconcile(
        db_session,
        make_source(mirrored.id, 3, sku_digest(["SKU1", "SKU2", "SKU-EXTRA"]),
                    partitions_payload(remoto)),
        1,
    )

    solo_de_magento = [d for d in report.diverging_partitions if d.mirror_count is None]
    assert [d.partition for d in solo_de_magento] == [partition_of("SKU-EXTRA")]
    assert solo_de_magento[0].magento_count == 1
    assert solo_de_magento[0].mirror_digest is None


def test_a_module_without_content_partitions_aborts_instead_of_reporting_no_drift(
    db_session, mirrored
):
    """Un módulo viejo (o uno al que alguien le quite el digest de contenido)
    no debe producir 'sin deriva': ese silencio ES el defecto H1."""
    with pytest.raises(RuntimeError, match="content_partitions"):
        reconcile(
            db_session,
            make_source(mirrored.id, 2, sku_digest(["SKU1", "SKU2"]), omit_partitions=True),
            1,
        )


def test_a_module_that_partitions_differently_aborts(db_session, mirrored):
    """Dos esquemas distintos comparan claves disjuntas: el resultado no
    significaría nada, ni el 'todo divergente' ni el 'nada que comparar'."""
    with pytest.raises(RuntimeError, match="particiones"):
        reconcile(
            db_session,
            make_source(mirrored.id, 2, sku_digest(["SKU1", "SKU2"]),
                        partitions_payload(MIRRORED_ROWS), partition_count=4096),
            1,
        )


# --------------------------------------------------------------------------
# El esquema en sí: reproducible idéntico en PHP y en Python. Los valores
# esperados se calcularon del otro lado y están fijados en
# magento-module/.../Test/Unit/Model/ContentDigestTest.php.
# --------------------------------------------------------------------------


def test_the_partition_of_a_sku_matches_the_php_algorithm():
    assert partition_of("SKU-A") == "0f"
    assert partition_of("SKU-C") == "0b"
    assert partition_of("SKU-2") == "71"
    assert partition_of("SKU-764") == "71"
    # UTF-8 sin normalizar: los bytes del SKU son la entrada.
    assert partition_of("SKU-ÑOÑO") == "eb"


def test_partitioning_is_by_bytes_and_not_by_collation():
    """`utf8mb4_general_ci` consideraría iguales estos dos SKUs. Son productos
    distintos y caen en particiones distintas — lo que un ORDER BY de SQL no
    podría reproducir (Ruling 1)."""
    assert partition_of("SKU-ALPHA") != partition_of("sku-alpha")


def test_the_partition_digest_matches_the_php_algorithm():
    """Los cuatro digests están fijados, con estos mismos datos, en
    ContentDigestTest::testPartitionDigestsMatchThePythonAlgorithm. Si alguien
    cambia el algoritmo de un solo lado, las dos suites fallan."""
    rows = [
        ("SKU-ÑOÑO", datetime(2026, 9, 6, 8, 30, tzinfo=UTC)),
        ("SKU-764", datetime(2026, 9, 4, 8, 30, tzinfo=UTC)),
        ("SKU-A", datetime(2026, 9, 1, 8, 30, tzinfo=UTC)),
        ("SKU-C", None),
        ("SKU-2", datetime(2026, 9, 2, 8, 30, tzinfo=UTC)),
    ]

    assert content_partitions(rows) == {
        "0b": (1, "9538a914923fb7f5e8ac7025f773606a379a86c6f7656275c2b79bbf6a17194f"),
        "0f": (1, "1432ee94047f9bf665ef47238a280a5e3a856654aa186aa00d1b4b83f8bd492b"),
        "71": (2, "0e8940b72b274ead34dd1a9a85fa5e9f2dcb1ca0250d534a8cd93c2e88536d81"),
        "eb": (1, "273148f21b478033b76bff8f276b36f1ceb058765d3096c316634aa7ae0e241d"),
    }


def test_the_digest_of_a_partition_does_not_depend_on_row_order():
    """Los dos lados leen sus filas de bases distintas, con órdenes distintos.
    Si el digest dependiera del orden de la consulta, no coincidirían nunca."""
    rows = [("SKU-2", MIRRORED_AT), ("SKU-764", MIRRORED_AT)]

    assert content_partitions(rows) == content_partitions(list(reversed(rows)))


# --------------------------------------------------------------------------
# El token del timestamp: la trampa. Los dos lados tienen que llegar al MISMO
# texto, y este lado no guarda el texto sino un timestamptz.
# --------------------------------------------------------------------------


def test_the_token_is_the_utc_rendering_whatever_the_session_offset():
    """psycopg devuelve el valor con el offset de la sesión de Postgres. Si el
    token se armara con ESE reloj, el texto no sería el que entregó MySQL y el
    digest reportaría deriva permanente por zona horaria — el modo de fallo
    que este proyecto ya pisó con el filtro de versión."""
    en_utc = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
    el_mismo_instante_en_asuncion = en_utc.astimezone(timezone(timedelta(hours=-4)))

    assert el_mismo_instante_en_asuncion.hour == 4, "la fixture no probaría nada sin desplazamiento"
    assert timestamp_token(el_mismo_instante_en_asuncion) == "2026-09-01 08:30:00"
    assert timestamp_token(en_utc) == "2026-09-01 08:30:00"


def test_an_unknown_timestamp_has_its_own_token_and_is_not_a_filler_date():
    assert timestamp_token(None) == "desconocido"


def test_the_token_drops_the_fractional_part_like_php_does():
    """La columna de Magento es `timestamp` sin fracción, pero un tenant puede
    haberla alterado. Los dos lados canonicalizan al segundo; si uno
    desempatara por microsegundos y el otro no, la deriva sería permanente."""
    assert timestamp_token(datetime(2026, 9, 1, 8, 30, 0, 123456, tzinfo=UTC)) == (
        "2026-09-01 08:30:00"
    )


def test_a_naive_datetime_is_read_as_utc_and_not_as_local_time():
    """`parse_magento_datetime` crea el valor con tzinfo=UTC, así que una fila
    sin zona sólo puede venir de otro camino; interpretarla como hora local
    introduciría un desplazamiento silencioso."""
    sin_zona = datetime(2026, 9, 1, 8, 30)  # noqa: DTZ001 -- el sin-zona ES el caso probado
    assert timestamp_token(sin_zona) == "2026-09-01 08:30:00"
