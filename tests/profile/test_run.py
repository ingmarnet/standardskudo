from skudo_testing import escribir, preparar, sembrar
from sqlalchemy import select

from skudo.profile.models import AttributeCoverage, ProfilePartition, ValueStats
from skudo.profile.run import profile_store_view


def test_la_pasada_encuentra_el_subtipo_y_lo_deja_escrito(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)

    run = profile_store_view(db_session, tenant.id, 1)

    particiones = db_session.scalars(
        select(ProfilePartition).where(ProfilePartition.run_id == run.id)
    ).all()
    assert {p.splitter_value for p in particiones} == {"ropa", "electro"}
    assert {p.splitter_key for p in particiones} == {"tipo"}
    assert all(p.decision_reason == "elegido" for p in particiones)


def test_la_cobertura_del_subtipo_separa_las_dos_fichas(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = profile_store_view(db_session, tenant.id, 1)

    filas = db_session.execute(
        select(ProfilePartition.splitter_value, AttributeCoverage.attribute_code,
               AttributeCoverage.coverage)
        .join(AttributeCoverage, AttributeCoverage.partition_id == ProfilePartition.id)
        .where(ProfilePartition.run_id == run.id)
    ).all()
    cobertura = {(v, c): cov for v, c, cov in filas}
    assert cobertura[("ropa", "talle")] == 1.0
    assert cobertura[("ropa", "voltaje")] == 0.0
    assert cobertura[("electro", "voltaje")] == 1.0


def test_dos_pasadas_sobre_el_mismo_espejo_dan_el_mismo_sello(db_session):
    """La propiedad que hace discutible una regla: si el perfil no es
    reproducible, cualquier desacuerdo sobre una regla acaba en 'a mí me salió
    distinto' y no hay forma de cerrarlo."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    primera = profile_store_view(db_session, tenant.id, 1)
    segunda = profile_store_view(db_session, tenant.id, 1)
    assert primera.digest == segunda.digest
    assert primera.id != segunda.id


def test_un_cambio_en_el_espejo_cambia_el_sello(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    antes = profile_store_view(db_session, tenant.id, 1)
    escribir(db_session, tenant, 1, "r0", {"tipo": "ropa"})
    despues = profile_store_view(db_session, tenant.id, 1)
    assert antes.digest != despues.digest


def test_las_dos_store_views_se_perfilan_por_separado(db_session):
    """El hallazgo que el producto existe para encontrar: el mismo atributo al
    100 % en PY y al 0 % en BR. Promediarlo lo borraría."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    for i in range(120):
        escribir(
            db_session,
            tenant,
            3,
            f"r{i}" if i < 60 else f"e{i - 60}",
            {"tipo": "ropa" if i < 60 else "electro"},
        )
    py = profile_store_view(db_session, tenant.id, 1)
    br = profile_store_view(db_session, tenant.id, 3)
    assert py.digest != br.digest
    assert py.store_view_magento_id == 1 and br.store_view_magento_id == 3


def test_los_productos_sin_attribute_set_van_a_su_propia_particion(db_session):
    """No se descartan y no se mezclan: se cuentan, porque un catálogo con
    muchos de ellos tiene un problema de ingesta que el perfil debe gritar."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    escribir(db_session, tenant, 1, "huerfano", {"tipo": "ropa"}, set_id=None)
    run = profile_store_view(db_session, tenant.id, 1)
    desconocida = db_session.scalars(
        select(ProfilePartition).where(
            ProfilePartition.run_id == run.id,
            ProfilePartition.splitter_kind == "set_desconocido",
        )
    ).one()
    assert desconocida.product_count == 1
    assert desconocida.attribute_set_id is None


def test_la_pasada_guarda_los_umbrales_con_los_que_midio(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = profile_store_view(db_session, tenant.id, 1)
    assert run.thresholds["MIN_PARTICION"] == 50
    assert run.thresholds["MAX_CARD"] == 12
    assert run.thresholds["MIN_GANANCIA"] == 0.05
    assert run.finished_at is not None


def test_un_nulo_del_espejo_no_es_un_valor(db_session):
    """Encontrado ejecutando el perfilador contra un espejo real: cuatro
    atributos de fecha guardaban `null`, y el filtro propio que la distribución
    usaba —`str(valor) != ""`— los dejaba pasar y escribía la cadena "None"
    como si fuera un valor observado del catálogo.

    La cobertura los contaba bien, porque preguntaba a `attribute_state`. Dos
    definiciones de "presente", y la de repuesto equivocada: el defecto C2 en
    otra forma."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    escribir(db_session, tenant, 1, "nulo", {"tipo": "ropa", "talle": None})

    run = profile_store_view(db_session, tenant.id, 1)
    fila = db_session.execute(
        select(ValueStats)
        .join(ProfilePartition, ProfilePartition.id == ValueStats.partition_id)
        .where(
            ProfilePartition.run_id == run.id,
            ProfilePartition.splitter_value == "ropa",
            ValueStats.attribute_code == "talle",
        )
    ).scalar_one()

    assert "None" not in [valor for valor, _ in fila.top_values]
    assert fila.n_present == 60


def test_las_dos_cuentas_de_presente_no_pueden_divergir(db_session):
    """Invariante de toda la pasada, y no de un caso: para cada atributo de
    cada partición, lo que la cobertura llama `presente` y lo que la
    distribución llama `n_present` son el mismo número. Un test por caso sólo
    habría atrapado el `null`; esta invariante atrapa la próxima divergencia
    sea cual sea su forma."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    escribir(db_session, tenant, 1, "nulo", {"tipo": "ropa", "talle": None})
    escribir(db_session, tenant, 1, "vacio", {"tipo": "ropa", "talle": ""})
    escribir(db_session, tenant, 1, "cero", {"tipo": "ropa", "talle": "0"})
    escribir(db_session, tenant, 1, "lista", {"tipo": "ropa", "talle": []})

    run = profile_store_view(db_session, tenant.id, 1)
    divergencias = db_session.execute(
        select(
            ProfilePartition.splitter_value,
            AttributeCoverage.attribute_code,
            AttributeCoverage.presente,
            ValueStats.n_present,
        )
        .join(AttributeCoverage, AttributeCoverage.partition_id == ProfilePartition.id)
        .join(
            ValueStats,
            (ValueStats.partition_id == ProfilePartition.id)
            & (ValueStats.attribute_code == AttributeCoverage.attribute_code),
        )
        .where(
            ProfilePartition.run_id == run.id,
            AttributeCoverage.presente != ValueStats.n_present,
        )
    ).all()

    assert divergencias == []
