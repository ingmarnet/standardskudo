<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Escribe en la cola de cambios (`standard_skudo_change_log`) que alimenta
 * el endpoint de deltas. Cada fila es un evento puntual (save o delete);
 * la tabla nunca se actualiza ni se borra, solo se inserta, para que
 * change_id sea un identificador monótono y seguro de paginar.
 *
 * Dos caminos de escritura, y la diferencia no es de estilo (H1):
 * `record()` para un evento de un producto (`catalog_product_save_after`),
 * `recordMany()` para los eventos de escritura MASIVA
 * (`catalog_product_attribute_update_before`,
 * `catalog_product_to_website_change`), que llegan con miles de productos de
 * una sola vez. Una fila por `INSERT` en ese caso convierte al observer en
 * el coste dominante de la acción que observa, y un observer caro es un
 * observer que alguien desactiva; con eso desaparece la única señal de esas
 * escrituras.
 */
class ChangeLog
{
    public const TABLE = 'standard_skudo_change_log';

    /**
     * Filas por `INSERT` multi-fila. Un lote entero en una sola sentencia
     * choca con `max_allowed_packet` en un catálogo grande, y ese error
     * llegaría como excepción dentro de la acción de admin del cliente.
     */
    public const CHUNK = 1000;

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

    /**
     * @param string[] $skus
     */
    public function recordMany(array $skus, string $event): void
    {
        if ($skus === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName(self::TABLE);

        foreach (array_chunk(array_values($skus), self::CHUNK) as $chunk) {
            $rows = [];
            foreach ($chunk as $sku) {
                $rows[] = ['sku' => (string) $sku, 'event' => $event];
            }
            $connection->insertMultiple($table, $rows);
        }
    }
}
