from skudo_testing import escribir, preparar, sembrar

from skudo.profile.report import profile_report
from skudo.profile.run import profile_store_view


def test_el_informe_dice_cuantos_sets_tienen_subtipo_y_por_que_no_los_demas(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    for i in range(70):
        escribir(db_session, tenant, 1, f"h{i}", {"tipo": "hogar"}, set_id=9)

    informe = profile_report(db_session, profile_store_view(db_session, tenant.id, 1))

    assert informe["productos"] == 190
    assert informe["sets"] == 2
    assert informe["con_subtipo"] == 1
    assert informe["razones"]["elegido"] == 1
    assert sum(informe["razones"].values()) == 2
    assert informe["divisores"] == {"tipo": 1}


def test_el_informe_cuenta_los_productos_sin_set(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    escribir(db_session, tenant, 1, "huerfano", {"tipo": "ropa"}, set_id=None)
    informe = profile_report(db_session, profile_store_view(db_session, tenant.id, 1))
    assert informe["sin_attribute_set"] == 1
