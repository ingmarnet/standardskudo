"""Utilidades compartidas por los tests. En `tests/` y no en `src/` porque no
son parte del producto: describen la forma del CABLE que el módulo Magento
emite, para que ningún mock vuelva a describir otra."""

import httpx


def skudo_response(payload: dict, status_code: int = 200) -> httpx.Response:
    """Respuesta HTTP con la forma que el módulo emite DE VERDAD.

    Standard_Skudo envuelve todo payload un nivel (`WebApiEnvelope::wrap()`,
    `return [$payload]`) porque `ServiceOutputProcessor::convertValue()`
    reindexa el primer nivel de todo `@return mixed[]` y, sin envoltorio,
    descarta las claves del payload sobre el cable — el hallazgo B1, que
    sobrevivió doce revisiones porque TODOS los mocks de este lado devolvían
    la forma sin envolver.

    Que exista este helper es lo que impide volver a escribir un mock con la
    forma equivocada por descuido; quien afirma el envoltorio contra el
    ServiceOutputProcessor real es
    `magento-module/.../Test/Unit/WebApi/ServiceOutputEnvelopeTest.php`, y
    quien afirma que el cliente lo exige es
    `tests/magento/test_client.py::test_a_bare_payload_is_rejected_...`.
    """
    return httpx.Response(status_code, json=[payload])
