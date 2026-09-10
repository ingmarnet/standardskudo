<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface SignalReaderInterface
{
    /**
     * Señales comerciales por store view, para que el lado Python (Task 12)
     * pueda ordenar hallazgos por dinero en vez de por prolijidad.
     *
     * POBLACIÓN (A1, contrato explícito): la respuesta se limita a los SKUs
     * que `/products` sirve para esta instancia — la población que Magento
     * considera activa — intersectada con los que tuvieron al menos una
     * venta en la ventana. Un SKU vendido cuyo producto ya no está activo
     * en el catálogo NO aparece. La razón: `sales_order_item` no es una
     * tabla versionada, así que sin este recorte el endpoint reportaba
     * señales de SKUs que ningún otro endpoint puede describir, y el espejo
     * quedaba con filas de `product_signal` sin un solo `product_record`
     * asociado. Ver `Model\SignalReader::restrictToCatalogPopulation()`.
     *
     * Cada item trae `null` (nunca 0, nunca "") cuando el dato es
     * DESCONOCIDO: `salable_qty` sin MSI o sin su tabla de índice,
     * `physical_qty` sin fila de stock, `margin` sin costo cargado,
     * `revenue` cuando algún ítem de pedido de la ventana no tiene importe
     * (`SUM()` salta los NULL y devolvería una cifra menor con aspecto
     * exacto), y `search_demand` cuando esa store view no tiene NI UNA fila
     * de `search_query` en la ventana. `null` y "cero real" son hechos
     * comerciales distintos y no deben colapsarse.
     *
     * `uses_msi` indica de qué mundo salieron los números de cantidad, para
     * que el lado Python no tenga que adivinar si un `salable_qty` nulo es
     * "no hay MSI" o "hay MSI pero no hay dato".
     *
     * `search_demand` es una atribución aproximada (ver
     * SignalReader::attachSearchDemand()), no una medición.
     *
     * B1 — TIPO NUMÉRICO SOBRE EL CABLE: los campos declarados `float|null`
     * (`revenue`, `salable_qty`, `physical_qty`, `margin`) llegan como número
     * JSON SIN parte fraccionaria cuando su valor es entero.
     * `json_encode((float) 300)` de PHP emite `300`, no `300.0`, con
     * `serialize_precision = -1` (el default desde PHP 7.1), así que la MISMA
     * columna llega como `int` o como `float` según el valor. Medido sobre
     * HTTP real: `revenue` 300 (int) en la store 1 y 75.5 (float) en la 3;
     * `salable_qty` 40 y 7, los dos int; `margin` 0.4545 y 0.3846, los dos
     * float.
     *
     * No se "arregla" forzando el tipo, porque las dos formas de forzarlo son
     * peores que documentarlo: emitirlo como string (`"300.0000"`) cambia el
     * tipo del contrato y obliga a parsear del otro lado, y multiplicar por
     * 1.0000001 o similar falsearía el dato. Lo que corresponde es que el
     * CONSUMIDOR acepte `int|float` donde el contrato dice `float`: las
     * columnas del espejo son `Numeric(18,4)` y no hay pérdida (verificado
     * por `test_an_integral_json_number_is_accepted_where_a_float_is_declared`
     * del lado Python). Un consumidor con validación estricta de tipos —un
     * pydantic con `strict=True`, un `isinstance(x, float)`— rechazaría el
     * 300, y este párrafo es lo que le dice por qué.
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
     *     revenue: float|null, salable_qty: float|null,
     *     physical_qty: float|null, uses_msi: bool, margin: float|null,
     *     search_demand: int|null}>}]
     */
    public function getSignals(int $storeId, int $days = 90): array;
}
