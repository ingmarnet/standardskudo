<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface EnvironmentProbeInterface
{
    /**
     * Describe el entorno Magento para que el ingestor se adapte a él.
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que el `foreach` devuelva la misma lista de un
     * elemento y el payload llegue intacto. El lado Python desenvuelve con
     * `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @return mixed[]
     */
    public function getProfile(): array;
}
