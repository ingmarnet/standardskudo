<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface AttributeSetReaderInterface
{
    /**
     * Los attribute sets de `catalog_product` con su nombre.
     *
     * Hasta este endpoint el espejo tenía el `attribute_set_id` de cada
     * producto pero NINGÚN nombre: un scope `attribute_set:4` en el panel no
     * podía identificarse. `eav_attribute_set.attribute_set_name` es la única
     * fuente del nombre, y no está en ninguna de las tablas que ya lee
     * `AttributeReader`.
     *
     * Sin paginación a propósito: los attribute sets son pocos (unos por
     * tenant, unos cientos en el catálogo más grande), a diferencia de los
     * 1.066 atributos o los 228.889 productos. Una sola lectura.
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que llegue intacto. El lado Python desenvuelve
     * con `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @return mixed[] [{"items": list<array{magento_id: int, name: string}>}]
     */
    public function getSets(): array;
}
