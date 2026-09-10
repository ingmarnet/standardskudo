<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * Hasta dónde leyó el ingestor, del lado del CLIENTE.
 *
 * M7. `standard_skudo_change_log` es insert-only por diseño y no tenía poda:
 * una sola importación masiva escribe 228k filas, y desde H1 —los observers
 * de escritura masiva— una acción del grid sobre 20.000 productos deja 20.000
 * filas donde antes dejaba cero. La tabla vive en la base de PRODUCCIÓN del
 * cliente, así que crecer sin límite no es un detalle de higiene.
 *
 * Podar es fácil; podar SIN BORRAR LO QUE NADIE LEYÓ es el problema, porque el
 * watermark del consumidor vive del otro lado del cable (en
 * `sync_watermark.last_change_id`, en nuestro Postgres) y esta base no lo
 * conoce.
 *
 * La respuesta que este proyecto eligió: el ingestor lo dice SOLO, en cada
 * lectura, sin llamada nueva y sin cooperación adicional.
 * `/deltas?sinceId=X` significa literalmente "dame los cambios con change_id
 * mayor que X", así que una petición con `sinceId = X` es prueba de que el
 * consumidor ya aplicó todo hasta X inclusive — y como `delta_sync` avanza su
 * watermark por página y sólo después de aplicarla, ese X es una cota
 * CONSERVADORA de lo consumido: nunca va por delante de lo aplicado. Se
 * guarda el máximo histórico y la poda no toca nada por encima.
 *
 * Tres consecuencias que conviene tener escritas:
 *
 * 1. Si el ingestor deja de leer, este número deja de moverse y la poda deja
 *    de borrar. La cola crece, que es el fallo BENIGNO; el maligno sería
 *    borrar lo que nadie leyó.
 * 2. Si dos consumidores distintos leyeran la misma instancia con watermarks
 *    distintos, gana el mayor y el más atrasado podría perder filas. En este
 *    diseño un tenant es un Magento y un ingestor; el margen de días de la
 *    poda es la segunda red. Queda declarado, no resuelto.
 * 3. Escribir en una lectura es incómodo a propósito: es lo que evita
 *    inventar un endpoint de escritura en un módulo de sólo lectura y evita
 *    que la poda dependa de que alguien se acuerde de informar el watermark.
 *    El fallo de esta escritura NUNCA rompe la lectura (ver `record()`).
 */
class DeltaReadWatermark
{
    public const TABLE = 'standard_skudo_delta_read';

    /** La tabla es un puntero, no un log: una fila, con esta clave. */
    public const ROW_ID = 1;

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    /**
     * Anota que el consumidor pidió los cambios posteriores a $sinceId.
     *
     * `GREATEST` y no una asignación: una petición con un sinceId más bajo
     * (un reintento, una segunda lectura de la misma página, un consumidor
     * recién inicializado) no debe hacer RETROCEDER el watermark — retroceder
     * no borraría nada de más, pero sí dejaría de podar para siempre.
     *
     * Un fallo acá se traga a propósito: esto es contabilidad para la poda, y
     * hacer fallar la lectura de deltas del cliente porque no se pudo
     * escribir un contador sería cambiar un problema de disco por una
     * sincronización detenida. El efecto de tragárselo es que el watermark se
     * queda atrás y la poda borra MENOS, que es la dirección segura;
     * `updated_at` deja ver desde cuándo no se mueve.
     */
    public function record(int $sinceId): void
    {
        if ($sinceId <= 0) {
            return;
        }

        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName(self::TABLE);

        try {
            $connection->query(
                sprintf(
                    'INSERT INTO %s (row_id, consumed_change_id) VALUES (%d, ?) '
                    . 'ON DUPLICATE KEY UPDATE consumed_change_id = '
                    . 'GREATEST(consumed_change_id, ?)',
                    $table,
                    self::ROW_ID
                ),
                [$sinceId, $sinceId]
            );
        } catch (\Throwable $error) {
            // Ver el docblock: la lectura del cliente no se rompe por esto.
            return;
        }
    }

    /**
     * El mayor change_id que el consumidor ya pidió, o 0 si nunca pidió nada.
     *
     * 0 significa "no se sabe que se haya consumido nada", y la poda con 0 no
     * borra ni una fila. Es la respuesta correcta para una instancia recién
     * instalada, o para una cuyo ingestor se apagó.
     */
    public function consumed(): int
    {
        $connection = $this->resource->getConnection();
        $select = $connection->select()
            ->from($this->resource->getTableName(self::TABLE), ['consumed_change_id'])
            ->where('row_id = ?', self::ROW_ID);

        return (int) $connection->fetchOne($select);
    }

    /** Cuándo se movió por última vez, o null si nunca. */
    public function observedAt(): ?string
    {
        $connection = $this->resource->getConnection();
        $select = $connection->select()
            ->from($this->resource->getTableName(self::TABLE), ['updated_at'])
            ->where('row_id = ?', self::ROW_ID);

        $value = $connection->fetchOne($select);
        return $value === false || $value === null ? null : (string) $value;
    }
}
