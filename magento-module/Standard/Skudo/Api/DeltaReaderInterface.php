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
     * @return mixed[]
     */
    public function getChanges(int $sinceId = 0, int $limit = 1000, ?int $sinceTimestamp = null): array;
}
