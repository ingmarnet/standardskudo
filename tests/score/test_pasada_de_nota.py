from skudo_testing import escribir, preparar, set_product_categories
from sqlalchemy import select

from skudo.findings.models import Finding, FindingRun
from skudo.findings.run import detect_store_view
from skudo.score.models import CatalogScore, ProductScore
from skudo.score.run import _hallazgos_por_sku, score_run, tendencia


def sembrar(session, tenant):
    # Publicado y sin foto: debería bajar la nota y marcarse como no publicable.
    escribir(session, tenant, 1, "sin-foto",
             {"name": "Paleta Nox", "status": "1", "visibility": "4", "price": "500000",
              "description": "d", "short_description": "s", "meta_title": "m"})
    # Publicado y completo.
    escribir(session, tenant, 1, "completo",
             {"name": "Paleta Bullpadel", "status": "1", "visibility": "4",
              "price": "600000", "image": "/a.jpg", "image_label": "Paleta Bullpadel",
              "description": "d", "short_description": "s", "meta_title": "m"})
    # Variante no navegable: el detector no la mira, y la nota tampoco debe.
    escribir(session, tenant, 1, "variante",
             {"name": "Paleta Nox T2", "status": "1", "visibility": "1"})
    # Con categoría: si no, `sin_categoria` descuenta y el caso deja de ser
    # "un producto al que sólo le falta la foto".
    for sku in ("sin-foto", "completo", "variante"):
        set_product_categories(session, tenant.id, sku, [7])


def test_la_nota_se_guarda_por_producto(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    score_run(db_session, detect_store_view(db_session, tenant.id, 1))

    notas = {
        n.sku: n
        for n in db_session.scalars(select(ProductScore).where(ProductScore.tenant_id == tenant.id))
    }
    assert notas["completo"].puntaje == 100 and notas["completo"].grado == "A"
    assert notas["sin-foto"].puntaje == 75 and notas["sin-foto"].grado == "C"
    assert notas["sin-foto"].critico is True
    assert notas["completo"].critico is False


def test_los_no_evaluados_no_entran_en_la_salud(db_session):
    """El error del denominador, que es de donde salió todo este proyecto: si
    la variante no navegable contara, sacaría 100 sin que nadie la mirara y
    subiría la salud del catálogo con un producto que no se evaluó."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    salud = score_run(db_session, detect_store_view(db_session, tenant.id, 1))

    assert salud.productos == 2, "sólo los publicados"
    assert salud.salud == 88, "promedio de 100 y 75, no de 100, 100 y 75"

    guardadas = db_session.scalars(
        select(ProductScore.sku).where(ProductScore.tenant_id == tenant.id)
    ).all()
    assert "variante" not in guardadas


def test_la_nota_explica_de_donde_sale(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    score_run(db_session, detect_store_view(db_session, tenant.id, 1))

    fila = db_session.scalars(
        select(ProductScore).where(ProductScore.sku == "sin-foto")
    ).one()
    assert [d["code"] for d in fila.deducciones] == ["sin_imagen"]
    assert sum(d["resta"] for d in fila.deducciones) == 100 - fila.puntaje


def test_un_hallazgo_de_grupo_baja_la_nota_de_cada_miembro(db_session):
    """Ocho talles sueltos son un hallazgo y ocho productos mal registrados."""
    tenant = preparar(db_session)
    base = "CALZADO ASICS GEL CHALLENGER 14 CLAY {} BLUE"
    for i, t in enumerate(["5,5'", "6'", "6,5'", "7'"]):
        escribir(db_session, tenant, 1, f"talle{i}",
                 {"name": base.format(t), "status": "1", "visibility": "4",
                  "price": "9", "image": "/a.jpg", "image_label": "Calzado",
                  "description": "d", "short_description": "s", "meta_title": "m"})
        set_product_categories(db_session, tenant.id, f"talle{i}", [7])
    score_run(db_session, detect_store_view(db_session, tenant.id, 1))

    notas = db_session.scalars(
        select(ProductScore).where(ProductScore.tenant_id == tenant.id)
    ).all()
    assert len(notas) == 4
    assert all(n.puntaje == 75 for n in notas), "el grupo penaliza a los cuatro"
    assert all(
        d["code"] == "variantes_por_talle" for n in notas for d in n.deducciones
    )


def test_la_nota_de_producto_se_reescribe_y_no_se_duplica(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    score_run(db_session, detect_store_view(db_session, tenant.id, 1))
    score_run(db_session, detect_store_view(db_session, tenant.id, 1))

    assert db_session.scalar(
        select(__import__("sqlalchemy").func.count()).select_from(ProductScore)
    ) == 2, "una fila por producto, no una por pasada"


def test_hallazgos_por_sku_respeta_el_orden_de_id_no_uno_incidental(db_session):
    """Sin ORDER BY, un empate exacto de severidad+causa deja el `code`
    representativo (el que gana el dedup por causa en `nota_de_producto`,
    "ante empate de peso, el primero que llegó") a merced del orden físico
    que Postgres decida devolver, que puede cambiar entre corridas. Con
    `.order_by(Finding.id)` el "primero que llegó" es siempre el de menor id,
    sin importar el orden alfabético de sus códigos ni nada más."""
    tenant = preparar(db_session)
    run = FindingRun(tenant_id=tenant.id, store_view_magento_id=1,
                      mirror_sync_generation=1, product_count=1)
    db_session.add(run); db_session.flush()

    # códigos a propósito en orden alfabético INVERSO al de inserción, para
    # que un test que dependiera del orden alfabético (o de cualquier orden
    # que no sea el id) fallara.
    db_session.add(Finding(run_id=run.id, code="zzz_ultimo", axis=3, severity="media",
                           subject_type="producto", subject_key="X",
                           evidence={"attribute": "color"}))
    db_session.add(Finding(run_id=run.id, code="aaa_primero", axis=3, severity="media",
                           subject_type="producto", subject_key="X",
                           evidence={"attribute": "color"}))
    db_session.flush()

    por_sku = _hallazgos_por_sku(db_session, run)
    codigos = [h.code for h in por_sku["X"]]
    assert codigos == ["zzz_ultimo", "aaa_primero"], (
        "el orden es el de inserción (id ascendente), no el alfabético"
    )

    # y es estable entre llamadas: dos pasadas sobre los mismos hallazgos
    # producen el mismo orden, siempre.
    otra = _hallazgos_por_sku(db_session, run)
    assert [h.code for h in otra["X"]] == codigos


def test_la_historia_del_catalogo_si_se_acumula(db_session):
    """La única pregunta que le interesa a dirección: ¿mejora o empeora?"""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    for _ in range(3):
        score_run(db_session, detect_store_view(db_session, tenant.id, 1))

    assert db_session.scalar(
        select(__import__("sqlalchemy").func.count()).select_from(CatalogScore)
    ) == 3
    serie = tendencia(db_session, tenant.id, 1)
    assert len(serie) == 3
    assert all(p["salud"] == 88 for p in serie)
    assert [p["medido_en"] for p in serie] == sorted(p["medido_en"] for p in serie), (
        "de la más vieja a la más nueva: una curva se lee hacia adelante"
    )
