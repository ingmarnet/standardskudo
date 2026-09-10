<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface AttributeReaderInterface
{
    /**
     * Lee una página de atributos de `catalog_product` con sus opciones y las
     * etiquetas de cada opción por store view.
     *
     * Sin este endpoint, `attributes`/`attribute_options`/`option_labels`/
     * `categories` (espejo) no tienen ningún camino de ingesta: sus funciones
     * de upsert solo se llaman desde tests. Este endpoint es el primero de
     * los dos que lo resuelven (el segundo, categorías, es la Task A2 y no
     * toca este módulo).
     *
     * Paginación por cursor sobre `attribute_id` (keyset, nunca offset): con
     * 1.066 atributos de este entity type y decenas de miles de opciones, una
     * respuesta sin paginar sería enorme, y las opciones solo se leen para
     * los atributos de la página actual.
     *
     *
     * La respuesta viaja ENVUELTA un nivel (`WebApiEnvelope::wrap()`):
     * `[<payload>]`, no `<payload>`. `ServiceOutputProcessor::convertValue()`
     * reindexa el primer nivel de todo `mixed[]` y descartaría las claves del
     * payload; envolverlo hace que el `foreach` devuelva la misma lista de un
     * elemento y el payload llegue intacto. El lado Python desenvuelve con
     * `response.json()[0]`. Ver `Model\WebApiEnvelope`.
     *
     * @param int $limit
     * @param string|null $cursor
     * @return mixed[] [{"items": list<array{code: string, label: string,
     *     frontend_input: string, declared_scope: string, is_filterable: bool,
     *     is_required: bool, attribute_set_ids: int[],
     *     options: list<array{option_id: int, labels: object}>}>,
     *     "next_cursor": string|null}]
     */
    public function getPage(int $limit = 500, ?string $cursor = null): array;
}
