<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface CategoryReaderInterface
{
    /**
     * Lee una página de categorías con su `path` como árbol de ids y su
     * estado (activa/nombre) YA RESUELTO por store view.
     *
     * Este es el segundo de los dos endpoints de la Enmienda C1: sin él,
     * `Category.path` y `CategoryStoreState.is_active` nunca se escriben, y
     * `derive_category_effect` (pura y correcta) queda inalcanzable desde
     * datos de espejo real.
     *
     * `path` viaja como lista de enteros, nunca como el string
     * "1/2/108/109" que guarda Magento: `derive_category_effect` decide
     * pertenencia al árbol con `root_category_id in assignment_path` y no
     * debe parsear strings para eso.
     *
     * `store_states` es una LISTA de objetos, no un mapa store_id -> datos:
     * un mapa con enteros consecutivos desde cero se serializaría como
     * array JSON del lado PHP (el mismo bug que ya afectó a las opciones de
     * atributo, Task A1), destruyendo la asociación store_id -> estado. Se
     * emite una entrada por CADA store view de la instancia, tenga o no
     * override, para que el lado Python nunca tenga que adivinar si una
     * entrada ausente significa "inactiva" o "desconocida".
     *
     * Paginación por cursor (keyset) sobre la clave de entidad resuelta por
     * EntityKeyResolver, nunca offset — misma razón que ProductReader.
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
     * @return mixed[] [{"items": list<array{category_id: int, path: int[],
     *     default_name: string, store_states: list<array{store_id: int,
     *     is_active: bool, name: string}>}>, "next_cursor": string|null}]
     */
    public function getPage(int $limit = 500, ?string $cursor = null): array;
}
