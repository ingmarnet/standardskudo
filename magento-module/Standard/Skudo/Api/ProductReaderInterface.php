<?php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface ProductReaderInterface
{
    /**
     * Lee una página de productos con sus valores global y de store view.
     *
     * @param int $storeId
     * @param int $limit
     * @param string|null $cursor
     * @return mixed[]
     */
    public function getPage(int $storeId, int $limit = 500, ?string $cursor = null): array;

    /**
     * Relee un conjunto concreto de SKUs. Complemento del endpoint de deltas:
     * la cola dice QUÉ cambió, esto trae el estado nuevo.
     *
     * POST (no GET): la identidad viaja como texto sin normalizar, así que un
     * SKU puede contener una coma, y `delta_sync` puede pedir hasta 1000 SKUs
     * de una vez, cuyo tamaño como query string un proxy típico rechaza.
     *
     * @param int $storeId
     * @param string[] $skus
     * @return mixed[]
     */
    public function getBySku(int $storeId, array $skus): array;
}
