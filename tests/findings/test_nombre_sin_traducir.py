"""Nombre sin traducir para la store view (Eje 1).

Un producto cuyo nombre repite el de la store view fuente (la de menor
`magento_id`) no se tradujo al idioma local. Solo mira store views de idioma
distinto: dos tiendas en español con el mismo nombre es correcto.
"""

from sqlalchemy import select

from skudo.findings.models import Finding
from skudo.findings.run import detect_store_view
from skudo.mirror.models import StoreView
from skudo_testing import escribir, preparar


def _store(session, tenant_id, magento_id, code, locale):
    session.add(StoreView(
        tenant_id=tenant_id, magento_id=magento_id, group_magento_id=magento_id,
        code=code, name=code, is_active=True, locale=locale, currency="USD",
    ))
    session.flush()


def _codigos(db_session, run):
    return {c for (c,) in db_session.execute(
        select(Finding.code).where(Finding.run_id == run.id).distinct()
    )}


def test_el_nombre_igual_al_de_la_fuente_se_marca(db_session):
    tenant = preparar(db_session)
    _store(db_session, tenant.id, 1, "py", "es_PY")
    _store(db_session, tenant.id, 2, "br", "pt_BR")
    escribir(db_session, tenant, 1, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})
    escribir(db_session, tenant, 2, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})

    assert "nombre_sin_traducir" in _codigos(db_session, detect_store_view(db_session, tenant.id, 2))


def test_la_fuente_no_se_marca_a_si_misma(db_session):
    tenant = preparar(db_session)
    _store(db_session, tenant.id, 1, "py", "es_PY")
    _store(db_session, tenant.id, 2, "br", "pt_BR")
    escribir(db_session, tenant, 1, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})
    escribir(db_session, tenant, 2, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})

    assert "nombre_sin_traducir" not in _codigos(db_session, detect_store_view(db_session, tenant.id, 1))


def test_el_nombre_traducido_no_se_marca(db_session):
    tenant = preparar(db_session)
    _store(db_session, tenant.id, 1, "py", "es_PY")
    _store(db_session, tenant.id, 2, "br", "pt_BR")
    escribir(db_session, tenant, 1, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})
    escribir(db_session, tenant, 2, "A", {"name": "Raquete de padel", "status": "1", "visibility": "4"})

    assert "nombre_sin_traducir" not in _codigos(db_session, detect_store_view(db_session, tenant.id, 2))


def test_mismo_idioma_no_se_marca(db_session):
    tenant = preparar(db_session)
    _store(db_session, tenant.id, 1, "py", "es_PY")
    _store(db_session, tenant.id, 2, "ar", "es_AR")
    escribir(db_session, tenant, 1, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})
    escribir(db_session, tenant, 2, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})

    assert "nombre_sin_traducir" not in _codigos(db_session, detect_store_view(db_session, tenant.id, 2))


def test_sin_par_de_stores_no_hay_hallazgo(db_session):
    tenant = preparar(db_session)
    _store(db_session, tenant.id, 1, "py", "es_PY")
    escribir(db_session, tenant, 1, "A", {"name": "Pala de pádel", "status": "1", "visibility": "4"})

    assert "nombre_sin_traducir" not in _codigos(db_session, detect_store_view(db_session, tenant.id, 1))
