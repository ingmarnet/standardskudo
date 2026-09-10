<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

/**
 * La poda de `standard_skudo_change_log` (M7).
 *
 * La tabla es insert-only por diseño —change_id monótono, seguro de paginar—
 * y no tenía retención: una importación masiva escribe 228k filas y, desde el
 * cierre de H1, una acción del grid sobre 20.000 productos escribe 20.000
 * donde antes escribía cero. Y vive en la base de PRODUCCIÓN del cliente.
 *
 * LA REGLA, que son DOS condiciones y las dos obligatorias:
 *
 *   change_id <= (lo que el ingestor ya consumió)
 *   AND changed_at < NOW() - INTERVAL <margen> DAY
 *
 * La primera es la que hace imposible borrar lo no leído: el número sale de
 * `DeltaReadWatermark`, que sólo se mueve cuando el ingestor PIDE los cambios
 * posteriores a él (ver esa clase para por qué es una cota conservadora).
 * Sin ingestor que lea, el número no se mueve y la poda no borra nada.
 *
 * La segunda no es redundante y no es decoración: es el margen que sobrevive
 * a que la primera se equivoque. Un consumidor mal configurado que pidiera
 * `sinceId` muy alto, un segundo consumidor con otro watermark (§2 del
 * docblock de `DeltaReadWatermark`), una restauración de nuestro Postgres a
 * un punto anterior — en todos esos casos el margen de días deja las filas
 * recientes en su lugar y da tiempo a notarlo. Por eso el margen es
 * configurable y su default es generoso (30 días) en vez de mínimo.
 *
 * El corte se calcula EN SQL (`DATE_SUB(NOW(), ...)`) y no en PHP:
 * `changed_at` lo escribe MySQL con su propio reloj y su propia zona, y
 * comparar contra una cadena compuesta en PHP es un desplazamiento silencioso
 * de horas en cuanto los dos relojes o las dos zonas no coincidan. Un solo
 * reloj, sin conversión.
 *
 * El predicado se escribe UNA vez y lo usan tanto el conteo del ensayo
 * (`--dry-run`) como el borrado: un ensayo que informara un conjunto distinto
 * del que el borrado toca sería peor que no tener ensayo.
 */
class ChangeLogRetention
{
    /**
     * Margen por defecto, en días. Generoso a propósito: el coste de guardar
     * un mes de cola es unos megabytes, y el de borrar una fila que el
     * ingestor no leyó es un cambio que el espejo no vuelve a ver nunca (no
     * hay evento que lo reemita, y la reconciliación sólo lo detecta si movió
     * `updated_at`).
     */
    public const DEFAULT_SAFETY_DAYS = 30;

    /** Ruta de configuración del margen. Ver `etc/config.xml` y `etc/system.xml`. */
    public const CONFIG_PATH_SAFETY_DAYS = 'standard_skudo/retention/change_log_days';

    /**
     * Piso del margen. Un margen de 0 días borraría lo que el ingestor acaba
     * de leer en la misma hora, dejando la primera condición como única red;
     * se rechaza en vez de obedecer.
     */
    public const MIN_SAFETY_DAYS = 1;

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly DeltaReadWatermark $watermark,
    ) {
    }

    /**
     * @return array{consumed_change_id: int, safety_days: int, queue_rows: int,
     *               deletable: int, deleted: int, dry_run: bool,
     *               watermark_updated_at: string|null}
     */
    public function prune(int $safetyDays = self::DEFAULT_SAFETY_DAYS, bool $dryRun = false): array
    {
        if ($safetyDays < self::MIN_SAFETY_DAYS) {
            throw new \InvalidArgumentException(sprintf(
                'el margen de seguridad no puede ser menor que %d día(s): con 0 '
                . 'la poda borraría lo que el ingestor acaba de leer',
                self::MIN_SAFETY_DAYS
            ));
        }

        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName(ChangeLog::TABLE);
        $consumed = $this->watermark->consumed();

        $result = [
            'consumed_change_id' => $consumed,
            'safety_days' => $safetyDays,
            'queue_rows' => (int) $connection->fetchOne(
                sprintf('SELECT COUNT(*) FROM %s', $table)
            ),
            'deletable' => 0,
            'deleted' => 0,
            'dry_run' => $dryRun,
            'watermark_updated_at' => $this->watermark->observedAt(),
        ];

        if ($consumed <= 0) {
            // Nada consumido que se sepa: no se borra ni una fila. El caso de
            // una instancia recién instalada y el de un ingestor apagado son
            // el mismo caso, y en los dos la respuesta correcta es no borrar.
            return $result;
        }

        $predicate = $this->predicate($safetyDays);
        $result['deletable'] = (int) $connection->fetchOne(
            sprintf('SELECT COUNT(*) FROM %s WHERE %s', $table, $predicate),
            [$consumed]
        );

        if ($dryRun) {
            return $result;
        }

        $statement = $connection->query(
            sprintf('DELETE FROM %s WHERE %s', $table, $predicate),
            [$consumed]
        );
        $result['deleted'] = (int) $statement->rowCount();

        return $result;
    }

    /**
     * El predicado, en un solo lugar. `%d` con un int ya validado: el margen
     * no puede llegar como texto desde ningún camino (el CLI lo castea, la
     * configuración también) y `INTERVAL ? DAY` no admite placeholder.
     */
    private function predicate(int $safetyDays): string
    {
        return sprintf(
            'change_id <= ? AND changed_at < DATE_SUB(NOW(), INTERVAL %d DAY)',
            $safetyDays
        );
    }
}
