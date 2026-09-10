<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

/**
 * Envoltorio obligatorio de TODO payload de web API de este módulo.
 *
 * Magento no serializa lo que un método de web API devuelve: lo pasa antes
 * por `Magento\Framework\Webapi\ServiceOutputProcessor::process()`, que lee
 * el `@return` del docblock de la interfaz y, para `mixed[]`, hace
 *
 *     foreach ($data as $datum) { $result[] = $datum; }
 *
 * REINDEXANDO el primer nivel. Un método que devolvía
 * `['items' => [...], 'next_cursor' => 'x']` llegaba al cliente como
 * `[[...], 'x']`: las claves de primer nivel desaparecían sobre el cable,
 * aunque el modelo y todas sus pruebas unitarias vieran el mapa correcto.
 *
 * Envolver un nivel neutraliza ese `foreach`: una lista de UN elemento se
 * reindexa a la misma lista de un elemento, y el payload —que es el
 * elemento, no el contenedor— viaja intacto, incluidos sus arrays y objetos
 * anidados (convertValue() no es recursivo sobre arrays). El lado Python
 * desenvuelve con `response.json()[0]`.
 *
 * Alternativas descartadas: devolver una cadena JSON (el estilo de
 * `Standard_AiChatbot`) obliga a doble codificación y pierde los tipos en la
 * firma; declarar interfaces `Api\Data` reales es correcto pero es una
 * reescritura del contrato completo, no un arreglo.
 *
 * `Test/Unit/WebApi/ServiceOutputEnvelopeTest` afirma esta convención contra
 * el ServiceOutputProcessor REAL, endpoint por endpoint, y falla si alguien
 * la revierte en cualquiera de ellos o agrega una ruta nueva sin cubrirla.
 *
 * @see \Standard\Skudo\Test\Unit\WebApi\ServiceOutputEnvelopeTest
 */
final class WebApiEnvelope
{
    /**
     * @param mixed[] $payload
     * @return mixed[]
     */
    public static function wrap(array $payload): array
    {
        return [$payload];
    }
}
