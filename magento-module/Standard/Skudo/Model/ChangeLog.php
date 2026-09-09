<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Escribe en la cola de cambios (`standard_skudo_change_log`) que alimenta
 * el endpoint de deltas. Cada fila es un evento puntual (save o delete);
 * la tabla nunca se actualiza ni se borra, solo se inserta, para que
 * change_id sea un identificador monótono y seguro de paginar.
 */
class ChangeLog
{
    public const TABLE = 'standard_skudo_change_log';

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function record(string $sku, string $event): void
    {
        $connection = $this->resource->getConnection();
        $connection->insert(
            $this->resource->getTableName(self::TABLE),
            ['sku' => $sku, 'event' => $event]
        );
    }
}
