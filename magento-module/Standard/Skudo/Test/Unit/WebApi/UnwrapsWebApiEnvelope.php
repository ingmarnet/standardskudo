<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\WebApi;

/**
 * Desenvuelve el `WebApiEnvelope::wrap()` de las respuestas del módulo, para
 * que las pruebas de los Model sigan afirmando el CONTENIDO del payload sin
 * repetir el `[0]` en cada línea.
 *
 * Es deliberadamente aserción y no un simple `[0]`: si alguien quitara el
 * envoltorio, estas pruebas fallarían acá con un mensaje que dice qué pasó,
 * en vez de leer una clave inexistente y fallar más abajo por otra razón.
 * Quien afirma el envoltorio EN SÍ —contra el ServiceOutputProcessor real— es
 * `ServiceOutputEnvelopeTest`, no este trait.
 */
trait UnwrapsWebApiEnvelope
{
    /**
     * @param mixed[] $response
     * @return mixed[]
     */
    private function payloadOf(array $response): array
    {
        $this->assertArrayHasKey(
            0,
            $response,
            'la respuesta debe venir envuelta un nivel (WebApiEnvelope::wrap)'
        );
        $this->assertCount(1, $response, 'el envoltorio es exactamente un elemento');
        $this->assertIsArray($response[0]);

        return $response[0];
    }
}
