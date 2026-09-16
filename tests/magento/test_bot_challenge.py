"""Un filtro de bots no es un error de Magento, y confundirlos cuesta una tarde.

Encontrado en el primer despliegue real: `renovapadel.com.py` está detrás de
Cloudflare, y el ingestor recibió un 403 con la página «Just a moment...». El
mensaje que llegó al operador eran cuatrocientos caracteres de cabecera CSP —
que invitan a revisar el token, que es exactamente lo que NO había que revisar.
"""

import httpx
import pytest

from skudo.magento.client import (
    USER_AGENT,
    BotChallengeError,
    MagentoApiError,
    MagentoClient,
    raise_for_status,
)

DESAFIO = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    '<meta http-equiv="content-security-policy" content="default-src \'none\'; '
    'script-src \'nonce-TSZWck3bMJAOHlNyDBeJTm\' https://challenges.cloudflare.com">'
)


def respuesta(status, texto, tipo, path="/rest/V1/skudo/environment"):
    return httpx.Response(
        status,
        text=texto,
        headers={"content-type": tipo},
        request=httpx.Request("GET", f"https://tienda.test{path}"),
    )


def test_un_desafio_de_navegador_se_nombra_como_lo_que_es():
    with pytest.raises(BotChallengeError) as exc:
        raise_for_status(respuesta(403, DESAFIO, "text/html; charset=UTF-8"))

    mensaje = str(exc.value)
    assert "filtro de bots" in mensaje
    assert "no" in mensaje and "Magento" in mensaje
    # El remedio viaja con el diagnóstico: sin esto, el operador sabe qué pasó
    # y no qué hacer.
    assert "autorizar la IP" in mensaje or "origen" in mensaje
    # Y NO se vuelca el HTML: es lo que escondía el hecho útil.
    assert "content-security-policy" not in mensaje
    assert "nonce-" not in mensaje


def test_un_403_legitimo_de_magento_conserva_su_mensaje():
    """El modo de fallo peligroso al revés: tratar como desafío un 403 real del
    módulo —token sin el recurso `Standard_Skudo::read`— mandaría a tocar el
    firewall cuando lo que falta es un permiso."""
    cuerpo = '{"message":"The consumer isn\'t authorized to access %resources.",' \
             '"parameters":{"resources":"Standard_Skudo::read"}}'
    with pytest.raises(MagentoApiError) as exc:
        raise_for_status(respuesta(403, cuerpo, "application/json"))

    assert not isinstance(exc.value, BotChallengeError)
    assert "Standard_Skudo::read" in str(exc.value)


def test_un_503_html_sin_marcas_de_desafio_no_se_confunde():
    """Un 503 de mantenimiento de Magento también es HTML. Sin las marcas, no
    se afirma que haya un filtro de bots: inventar la causa es peor que no
    tenerla."""
    with pytest.raises(MagentoApiError) as exc:
        raise_for_status(respuesta(503, "<html><body>Service Unavailable</body></html>", "text/html"))
    assert not isinstance(exc.value, BotChallengeError)


def test_un_500_con_html_de_desafio_no_cuenta():
    """Los desafíos llegan como 403, 429 o 503. Un 500 con esas palabras es
    otra cosa y no se le pone esa etiqueta."""
    with pytest.raises(MagentoApiError) as exc:
        raise_for_status(respuesta(500, DESAFIO, "text/html"))
    assert not isinstance(exc.value, BotChallengeError)


def test_el_ingestor_se_identifica_con_su_nombre_y_un_contacto():
    """Un cliente identificable se puede autorizar por nombre en un WAF. El
    default de la librería —`python-httpx/0.x`— es una firma anónima, que es lo
    que un filtro de bots está entrenado para frenar."""
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["ua"] = request.headers.get("user-agent")
        visto["accept"] = request.headers.get("accept")
        visto["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[{"edition": "Community"}])

    cliente = MagentoClient(
        "https://tienda.test", "tok", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(Exception):
        cliente.environment()

    assert visto["ua"] == USER_AGENT
    assert "StandardSkudo" in visto["ua"]
    assert "github.com" in visto["ua"], "sin un contacto, identificarse no sirve de nada"
    assert "python-httpx" not in visto["ua"]
    assert visto["accept"] == "application/json"
    assert visto["auth"] == "Bearer tok"


def test_el_user_agent_es_ascii():
    """Una cabecera HTTP no admite caracteres fuera de ASCII: httpx se niega a
    enviarla y la petición muere antes de salir. Un acento en esta constante
    rompe TODAS las peticiones del ingestor, no una — que es como se encontró,
    la primera vez que este archivo corrió."""
    USER_AGENT.encode("ascii")
