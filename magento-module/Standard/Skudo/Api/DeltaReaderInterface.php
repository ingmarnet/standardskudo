<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface DeltaReaderInterface
{
    /**
     * Lee cambios de la cola desde `sinceId` (exclusivo), en orden ascendente
     * de change_id. La paginación es SIEMPRE por change_id (monótono) y
     * nunca por timestamp: dos cambios en el mismo segundo se perderían.
     *
     * @param int $sinceId
     * @param int $limit
     * @param int|null $sinceTimestamp Unix seconds. Cuando el esquema tiene
     *     created_in/updated_in (Magento_Staging) y se pasa este parámetro,
     *     la respuesta también incluye los SKUs cuya versión activa comenzó
     *     dentro de la ventana que el caller todavía no vio, con
     *     `change_id: 0` y `event: "save"` — ver Model\DeltaReader::getChanges()
     *     para el porqué (un observer no puede detectar el paso del tiempo).
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que el `foreach` devuelva la misma lista de un
     * elemento y el payload llegue intacto. El lado Python desenvuelve con
     * `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @return mixed[] [{"items": list<array{change_id: int, sku: string,
     *     event: string, changed_at: string}>, "last_change_id": int|null}]
     */
    public function getChanges(int $sinceId = 0, int $limit = 1000, ?int $sinceTimestamp = null): array;
}
