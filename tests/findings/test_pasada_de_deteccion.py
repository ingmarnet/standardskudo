from skudo_testing import escribir, preparar
from sqlalchemy import select

from skudo.findings.models import DetectorCoverage, Finding
from skudo.findings.run import detect_store_view, findings_report


def sembrar(session, tenant):
    # Un publicado sin imagen, una variante sin imagen (que NO es defecto),
    # un configurable sin precio (que tampoco lo es) y tres talles sueltos.
    escribir(session, tenant, 1, "pub-sin-foto",
             {"name": "Paleta Nox", "status": "1", "visibility": "4"})
    escribir(session, tenant, 1, "variante",
             {"name": "Paleta Nox T2", "status": "1", "visibility": "1"})
    escribir(session, tenant, 1, "config",
             {"name": "Zapatilla Padre", "status": "1", "visibility": "4",
              "image": "/a.jpg"})
    for i in range(3):
        escribir(session, tenant, 1, f"talle{i}",
                 {"name": "CALZADO ASICS GEL CHALLENGER", "status": "1",
                  "visibility": "4", "image": "/b.jpg", "price": "500000"})
    session.execute(
        __import__("sqlalchemy").text(
            "update product_record set type_id='configurable' where sku='config'"
        )
    )


def test_la_pasada_guarda_hallazgos_y_cobertura(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)

    run = detect_store_view(db_session, tenant.id, 1)

    codigos = {
        c for (c,) in db_session.execute(
            select(Finding.code).where(Finding.run_id == run.id).distinct()
        )
    }
    assert "sin_imagen" in codigos
    assert "variantes_sueltas" in codigos

    coberturas = db_session.scalars(
        select(DetectorCoverage).where(DetectorCoverage.run_id == run.id)
    ).all()
    assert len(coberturas) == 8, "los ocho detectores declaran cobertura, siempre"
    assert all(c.evaluados + c.no_aplica + c.no_evaluado == run.product_count
               for c in coberturas)


def test_el_configurable_no_aparece_como_sin_precio(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = detect_store_view(db_session, tenant.id, 1)
    sin_precio = [
        f.subject_key for f in db_session.scalars(
            select(Finding).where(Finding.run_id == run.id, Finding.code == "sin_precio")
        )
    ]
    assert "config" not in sin_precio


def test_el_informe_lleva_el_denominador_pegado_al_numero(db_session):
    """Un hallazgo sin su cobertura al lado es un porcentaje esperando el
    denominador equivocado."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    informe = findings_report(db_session, detect_store_view(db_session, tenant.id, 1))

    assert informe["productos"] == 6
    for h in informe["por_deteccion"]:
        assert h["evaluados"] is not None, h["code"]
        assert h["porcentaje"] is not None, h["code"]
    assert informe["exclusiones"], "las exclusiones viajan en el informe"


def test_una_variante_no_navegable_no_genera_hallazgo_de_imagen(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = detect_store_view(db_session, tenant.id, 1)
    sin_img = {
        f.subject_key for f in db_session.scalars(
            select(Finding).where(Finding.run_id == run.id, Finding.code == "sin_imagen")
        )
    }
    assert sin_img == {"pub-sin-foto"}
