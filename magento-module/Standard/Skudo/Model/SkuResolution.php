<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

/**
 * Resultado de `SkuResolver::resolve()`: los SKUs que resolvieron y los ids
 * que NO.
 *
 * Los ids sin resolver viajan como parte del resultado, y no se descartan
 * dentro del resolutor, porque un id que no resuelve es un CAMBIO PERDIDO:
 * la acción masiva escribió algo y la cola no va a enterarse. Quien llama
 * tiene que poder dejar rastro de eso. Un resolutor que devolviera sólo los
 * SKUs buenos convertiría una pérdida en un silencio, que es la forma del
 * defecto H1.
 */
final class SkuResolution
{
    /**
     * @param string[] $skus SKUs distintos, ordenados con SORT_STRING
     * @param int[] $unresolvedIds entity_ids sin ninguna fila
     */
    public function __construct(
        public readonly array $skus,
        public readonly array $unresolvedIds,
    ) {
    }
}
