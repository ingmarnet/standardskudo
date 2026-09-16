"""El ciclo sobre todos los tenants, y sobre todo su AISLAMIENTO.

Una plataforma que hay que arrancar una vez por cliente no es una plataforma.
Y una que se detiene entera porque el Magento de un cliente está caído, tampoco:
el aislamiento por fila que el espejo ya tenía no sirve de nada si la operación
diaria es un solo proceso que se cae con el primer tenant que falle.
"""

import httpx
import pytest
from skudo_testing import checksums_payload, escribir, skudo_response
from sqlalchemy import text
from sqlalchemy.orm import Session

from skudo.mirror.models import Tenant
from skudo.operate.cycle import ciclo_de_todos


def transporte(fallar_para: set[str] | None = None, skus: list[str] | None = None):
    """Un Magento de mentira que falla para los hosts que se le indiquen.

    Los checksums se arman con el ayudante del producto y no a mano: una forma
    inventada acá haría fallar la reconciliación por un motivo que no existe en
    la realidad, y ya pasó — costó una ronda de diagnóstico.
    """
    fallar_para = fallar_para or set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in fallar_para:
            raise httpx.ConnectError("el Magento de este cliente está caído")
        if request.url.path.endswith("/deltas"):
            return skudo_response({"items": [], "last_change_id": 0})
        if request.url.path.endswith("/checksums"):
            return skudo_response(checksums_payload(skus or []))
        return skudo_response({})

    return httpx.MockTransport(handler)


def _vaciar(engine):
    """`TRUNCATE ... CASCADE` y no `DELETE FROM tenant`.

    Estos tests NO pueden usar el fixture de sesión con rollback: el ciclo
    hace commit por tenant, que es justo la propiedad que se está probando.
    Así que limpian de verdad, y con CASCADE porque el ciclo deja filas que
    referencian al tenant —marcas de agua, hallazgos— y un DELETE a secas
    choca contra la clave foránea.
    """
    with engine.begin() as c:
        c.execute(text("TRUNCATE tenant CASCADE"))


@pytest.fixture
def tres_tenants(migrated_engine, monkeypatch):
    _vaciar(migrated_engine)
    with Session(migrated_engine) as s:
        for code, host in [("uno", "uno.test"), ("dos", "dos.test"), ("tres", "tres.test")]:
            s.add(Tenant(code=code, name=code, base_url=f"https://{host}",
                         token_env_var=f"TOK_{code.upper()}"))
            monkeypatch.setenv(f"TOK_{code.upper()}", "x")
        s.commit()
    yield lambda: Session(migrated_engine)
    _vaciar(migrated_engine)


def test_el_ciclo_recorre_todos_los_tenants_sin_nombrar_ninguno(tres_tenants):
    ciclo = ciclo_de_todos(tres_tenants, transport=transporte(), detectar=False)
    assert [t.code for t in ciclo.tenants] == ["dos", "tres", "uno"]
    assert ciclo.fallidos == []


def test_un_tenant_caido_no_detiene_a_los_demas(tres_tenants):
    """La propiedad que define a la plataforma. Sin esto, el Magento de un
    cliente en mantenimiento deja sin actualizar a los otros cuatro."""
    ciclo = ciclo_de_todos(
        tres_tenants, transport=transporte({"dos.test"}), detectar=False
    )

    assert ciclo.fallidos == ["dos"]
    ok = [t.code for t in ciclo.tenants if t.ok]
    assert ok == ["tres", "uno"], "los sanos se procesan igual"


def test_el_fallo_dice_en_que_paso_se_corto(tres_tenants):
    """«Falló» no alcanza: la diferencia entre revisar el token y revisar la red
    es la diferencia entre diez minutos y una tarde."""
    ciclo = ciclo_de_todos(
        tres_tenants, transport=transporte({"dos.test"}), detectar=False
    )
    fallido = next(t for t in ciclo.tenants if not t.ok)
    assert fallido.paso_fallido == "delta-sync"
    assert "caído" in fallido.error or "Connect" in fallido.error


def test_un_token_ausente_se_reporta_como_tal_y_sin_revelarlo(tres_tenants, monkeypatch):
    monkeypatch.delenv("TOK_DOS", raising=False)
    ciclo = ciclo_de_todos(tres_tenants, transport=transporte(), detectar=False)

    fallido = next(t for t in ciclo.tenants if not t.ok)
    assert fallido.code == "dos"
    assert fallido.paso_fallido == "token"
    assert "TOK_DOS" in fallido.error, "nombra la variable que falta"
    assert "x" not in fallido.error.replace("TOK_DOS", ""), "nunca el valor"


def test_cada_tenant_usa_su_propia_transaccion(tres_tenants):
    """Con una sesión compartida, el fallo de uno deja la transacción inválida y
    los siguientes revientan al primer flush sin haber hecho nada mal. El
    aislamiento tiene que llegar hasta la transacción."""
    ciclo = ciclo_de_todos(
        tres_tenants, transport=transporte({"uno.test"}), detectar=False
    )
    # `uno` es el ÚLTIMO por orden alfabético: si el fallo contaminara la
    # transacción, no se notaría. Se comprueba al revés, con el primero.
    ciclo2 = ciclo_de_todos(
        tres_tenants, transport=transporte({"dos.test"}), detectar=False
    )
    assert ciclo.fallidos == ["uno"] and len([t for t in ciclo.tenants if t.ok]) == 2
    assert ciclo2.fallidos == ["dos"] and len([t for t in ciclo2.tenants if t.ok]) == 2


def test_el_resumen_no_obliga_a_leer_todo(tres_tenants):
    ciclo = ciclo_de_todos(tres_tenants, transport=transporte({"dos.test"}), detectar=False)
    resumen = ciclo.resumen()
    assert resumen["tenants"] == 3
    assert resumen["con_error"] == ["dos"]
    assert resumen["detalle"]["dos"]["ok"] is False
    assert resumen["detalle"]["uno"]["ok"] is True


def test_se_puede_acotar_a_algunos_tenants(tres_tenants):
    ciclo = ciclo_de_todos(tres_tenants, transport=transporte(), detectar=False,
                           codigos=["uno"])
    assert [t.code for t in ciclo.tenants] == ["uno"]


def test_el_ciclo_deja_un_informe_por_tenant_y_por_tienda(tres_tenants, tmp_path):
    """El informe también es de la plataforma: un directorio, un archivo por
    cada par (tenant, store view), con el nombre puesto por el sistema para que
    dos clientes no puedan pisarse."""
    engine_session = tres_tenants
    with engine_session() as s:
        tenant = s.scalars(__import__("sqlalchemy").select(Tenant)).first()
        escribir(s, tenant, 1, "sku1",
                 {"name": "Paleta", "status": "1", "visibility": "4"})
        s.commit()

    ciclo = ciclo_de_todos(engine_session, transport=transporte(skus=["sku1"]),
                           informes_en=tmp_path)

    assert ciclo.fallidos == []
    archivos = sorted(p.name for p in tmp_path.glob("*.html"))
    assert archivos, "el ciclo deja los informes escritos"
    assert all("-" in a and a.endswith(".html") for a in archivos)


def test_una_clave_ausente_que_no_es_el_token_no_se_reporta_como_token(tres_tenants):
    """Capturar `KeyError` a secas hacía que cualquier clave ausente —una del
    payload del módulo, por ejemplo— dijera «falta la variable de entorno»,
    mandando a revisar credenciales que estaban perfectas."""
    def rompe(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deltas"):
            return skudo_response({"items": [], "last_change_id": 0})
        # Un payload de checksums al que le falta una clave.
        return skudo_response({"partitions": [{"prefix": "00"}], "product_count": 1})

    ciclo = ciclo_de_todos(tres_tenants, transport=httpx.MockTransport(rompe),
                           detectar=False)
    for t in ciclo.tenants:
        if not t.ok:
            assert t.paso_fallido != "token", t.error
            assert "variable de entorno" not in (t.error or "")
