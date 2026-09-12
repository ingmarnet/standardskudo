import pytest
from sqlalchemy.exc import IntegrityError

from skudo.mirror.models import Tenant
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun


def _tenant(session) -> Tenant:
    tenant = Tenant(
        code="acme", name="Acme", base_url="http://acme.test", token_env_var="X"
    )
    session.add(tenant)
    session.flush()
    return tenant


def test_una_pasada_guarda_sus_umbrales_y_su_generacion(db_session):
    tenant = _tenant(db_session)
    run = ProfileRun(
        tenant_id=tenant.id,
        store_view_magento_id=1,
        mirror_sync_generation=7,
        thresholds={"MIN_PARTICION": 50},
        product_count=0,
    )
    db_session.add(run)
    db_session.flush()

    assert run.digest is None, "una pasada sin terminar no tiene sello"
    assert run.finished_at is None
    assert run.thresholds["MIN_PARTICION"] == 50


def test_la_cobertura_es_nula_cuando_no_hay_denominador(db_session):
    """`None` y `0.0` son cosas distintas y la columna tiene que poder decirlo."""
    tenant = _tenant(db_session)
    run = ProfileRun(
        tenant_id=tenant.id,
        store_view_magento_id=1,
        mirror_sync_generation=1,
        thresholds={},
        product_count=0,
    )
    db_session.add(run)
    db_session.flush()
    particion = ProfilePartition(
        run_id=run.id,
        attribute_set_id=4,
        splitter_kind="ninguno",
        splitter_value=None,
        product_count=3,
        ambiguity=None,
        decision_reason="sin candidatos",
    )
    db_session.add(particion)
    db_session.flush()
    fila = AttributeCoverage(
        partition_id=particion.id,
        attribute_code="color",
        presente=0,
        vacio=0,
        no_aplica=0,
        desconocido=3,
        coverage=None,
    )
    db_session.add(fila)
    db_session.flush()

    assert fila.coverage is None


def test_un_atributo_no_puede_repetirse_en_la_misma_particion(db_session):
    tenant = _tenant(db_session)
    run = ProfileRun(
        tenant_id=tenant.id,
        store_view_magento_id=1,
        mirror_sync_generation=1,
        thresholds={},
        product_count=0,
    )
    db_session.add(run)
    db_session.flush()
    particion = ProfilePartition(
        run_id=run.id,
        attribute_set_id=4,
        splitter_kind="ninguno",
        splitter_value=None,
        product_count=1,
        ambiguity=None,
        decision_reason="sin candidatos",
    )
    db_session.add(particion)
    db_session.flush()
    for _ in range(2):
        db_session.add(
            AttributeCoverage(
                partition_id=particion.id,
                attribute_code="color",
                presente=1,
                vacio=0,
                no_aplica=0,
                desconocido=0,
                coverage=1.0,
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()
