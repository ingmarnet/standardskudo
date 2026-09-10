<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface SignalReaderInterface
{
    /**
     * Señales comerciales por store view, para que el lado Python (Task 12)
     * pueda ordenar hallazgos por dinero en vez de por prolijidad.
     *
     * Cada item trae `null` (nunca 0, nunca "") cuando el dato es
     * DESCONOCIDO: `salable_qty` sin MSI o sin su tabla de índice,
     * `physical_qty` sin fila de stock, `margin` sin costo cargado. `null`
     * y "cero real" son hechos comerciales distintos y no deben colapsarse.
     *
     * `uses_msi` indica de qué mundo salieron los números de cantidad, para
     * que el lado Python no tenga que adivinar si un `salable_qty` nulo es
     * "no hay MSI" o "hay MSI pero no hay dato".
     *
     * `search_demand` es una atribución aproximada (ver
     * SignalReader::attachSearchDemand()), no una medición.
     *
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que el `foreach` devuelva la misma lista de un
     * elemento y el payload llegue intacto. El lado Python desenvuelve con
     * `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @param int $storeId
     * @param int $days Ventana de la agregación de ventas y de search_query.
     * @return mixed[] [{"items": list<array{sku: string, units_sold: int,
     *     revenue: float, salable_qty: float|null, physical_qty: float|null,
     *     uses_msi: bool, margin: float|null, search_demand: int}>}]
     */
    public function getSignals(int $storeId, int $days = 90): array;
}
