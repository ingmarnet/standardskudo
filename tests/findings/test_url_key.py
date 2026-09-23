"""URL key (Eje 9): ausente, con basura o duplicada en masa."""

from sqlalchemy import select

from skudo.findings.models import Finding
from skudo.findings.run import detect_store_view
from skudo_testing import escribir, preparar


def _codigos(db_session, run):
    return {c for (c,) in db_session.execute(
        select(Finding.code).where(Finding.run_id == run.id).distinct()
    )}


def _subjects(db_session, run, code):
    return {k for (k,) in db_session.execute(
        select(Finding.subject_key).where(
            Finding.run_id == run.id, Finding.code == code
        )
    )}


def test_url_key_ausente_se_marca(db_session):
    tenant = preparar(db_session)
    escribir(db_session, tenant, 1, "A", {"name": "Pala", "status": "1", "visibility": "4"})

    assert "url_key_ausente" in _codigos(db_session, detect_store_view(db_session, tenant.id, 1))


def test_url_key_limpia_no_se_marca(db_session):
    tenant = preparar(db_session)
    escribir(db_session, tenant, 1, "A", {
        "name": "Pala", "status": "1", "visibility": "4", "url_key": "pala-de-padel",
    })

    codigos = _codigos(db_session, detect_store_view(db_session, tenant.id, 1))
    assert "url_key_ausente" not in codigos
    assert "url_key_basura" not in codigos


def test_url_key_con_basura_se_marca(db_session):
    tenant = preparar(db_session)
    escribir(db_session, tenant, 1, "A", {
        "name": "Pala", "status": "1", "visibility": "4", "url_key": "Pala de Pádel_NUEVA",
    })

    run = detect_store_view(db_session, tenant.id, 1)
    assert "A" in _subjects(db_session, run, "url_key_basura")


def test_variante_no_navegable_no_se_marca(db_session):
    tenant = preparar(db_session)
    escribir(db_session, tenant, 1, "A", {
        "name": "Pala", "status": "1", "visibility": "1", "url_key": "Pala de Pádel",
    })

    codigos = _codigos(db_session, detect_store_view(db_session, tenant.id, 1))
    assert "url_key_ausente" not in codigos
    assert "url_key_basura" not in codigos


def test_url_key_duplicada_en_masa_se_marca(db_session):
    tenant = preparar(db_session)
    for i in range(10):
        escribir(db_session, tenant, 1, f"A{i}", {
            "name": f"Pala {i}", "status": "1", "visibility": "4", "url_key": "mismo-key",
        })

    run = detect_store_view(db_session, tenant.id, 1)
    assert "url_key_duplicada" in _codigos(db_session, run)


def test_url_key_duplicada_por_debajo_del_umbral_no_se_marca(db_session):
    tenant = preparar(db_session)
    for i in range(9):
        escribir(db_session, tenant, 1, f"A{i}", {
            "name": f"Pala {i}", "status": "1", "visibility": "4", "url_key": "mismo-key",
        })

    codigos = _codigos(db_session, detect_store_view(db_session, tenant.id, 1))
    assert "url_key_duplicada" not in codigos
