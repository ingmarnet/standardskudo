<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Standard\Skudo\Api\DeltaReaderInterface;

class DeltaReader implements DeltaReaderInterface
{
    private const MAX_LIMIT = 5000;

    /**
     * Si `catalog_product_entity` tiene created_in/updated_in
     * (Magento_Staging). Memoizado por la misma razón que
     * EntityKeyResolver/ProductReader: se prueba una sola vez por instancia,
     * nunca a partir de la edición.
     */
    private ?bool $versioningColumnsPresent = null;

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function getChanges(int $sinceId = 0, int $limit = 1000, ?int $sinceTimestamp = null): array
    {
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $connection = $this->resource->getConnection();

        // Se pagina por change_id, que es monótono. Paginar por changed_at
        // perdería cambios cuando dos ocurren en el mismo segundo.
        $select = $connection->select()
            ->from($this->resource->getTableName(ChangeLog::TABLE))
            ->where('change_id > ?', $sinceId)
            ->order('change_id ASC')
            ->limit($limit);

        $rows = $connection->fetchAll($select);

        $items = array_map(
            static fn (array $row): array => [
                'change_id' => (int) $row['change_id'],
                'sku' => (string) $row['sku'],
                'event' => (string) $row['event'],
                'changed_at' => (string) $row['changed_at'],
            ],
            $rows
        );

        // El watermark solo avanza con filas reales de la cola: las de
        // activación de versión (abajo) llevan change_id 0 a propósito y no
        // deben moverlo, así que last_change_id se calcula ANTES de unirlas.
        $lastChangeId = $rows === [] ? null : (int) end($rows)['change_id'];

        if ($sinceTimestamp !== null && $this->hasVersioningColumns($connection)) {
            foreach ($this->activatedVersions($connection, $sinceTimestamp) as $row) {
                $items[] = [
                    'change_id' => 0,
                    'sku' => (string) $row['sku'],
                    'event' => 'save',
                    'changed_at' => gmdate('Y-m-d H:i:s', (int) $row['created_in']),
                ];
            }
        }

        return [
            'items' => $items,
            'last_change_id' => $lastChangeId,
        ];
    }

    /**
     * Con Magento_Staging, una actualización programada crea su fila de
     * versión al programarse (con `catalog_product_save_after`, que sí
     * dispara el observer) pero se vuelve ACTIVA más tarde, cuando su
     * `created_in` pasa — y en ese momento no ocurre ningún evento de
     * Magento: el tiempo simplemente transcurre. Un observer no puede
     * suscribirse al paso del tiempo, así que la cola nunca se enteraría de
     * la activación y el espejo serviría el valor viejo indefinidamente
     * (la reconciliación tampoco lo detecta: compara conjuntos de SKU, no
     * valores). Por eso esta ventana se resuelve acá, en cada lectura de
     * deltas, comparando contra "ahora" en vez de depender de un evento.
     *
     * @return mixed[]
     */
    private function activatedVersions(AdapterInterface $connection, int $sinceTimestamp): array
    {
        $entity = $this->resource->getTableName('catalog_product_entity');
        $select = $connection->select()
            ->from($entity, ['sku', 'created_in'])
            ->where('created_in > ?', $sinceTimestamp)
            ->where('created_in <= UNIX_TIMESTAMP()')
            ->where('updated_in > UNIX_TIMESTAMP()');

        return $connection->fetchAll($select);
    }

    /**
     * Detección SIEMPRE por tableColumnExists(), nunca por edición ni por
     * lista de módulos: en Community estas columnas no existen y
     * referenciarlas sería un error de SQL, así que esa rama debe ser real,
     * no solo una condición que nunca se toma.
     */
    private function hasVersioningColumns(AdapterInterface $connection): bool
    {
        if ($this->versioningColumnsPresent === null) {
            $entity = $this->resource->getTableName('catalog_product_entity');
            $this->versioningColumnsPresent = $connection->tableColumnExists($entity, 'created_in')
                && $connection->tableColumnExists($entity, 'updated_in');
        }

        return $this->versioningColumnsPresent;
    }
}
