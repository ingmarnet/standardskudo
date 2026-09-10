<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Standard\Skudo\Api\DeltaReaderInterface;

class DeltaReader implements DeltaReaderInterface
{
    private const MAX_LIMIT = 5000;

    public function __construct(
        private readonly ResourceConnection $resource,
        // Inyectado, no instanciado con `new`: es la MISMA clase que usa
        // ProductReader (Task 8) para la misma pregunta de esquema y la
        // misma ventana de versión activa. Ver ActiveVersionResolver para
        // el porqué de tener un único lugar que la responda.
        private readonly ActiveVersionResolver $activeVersionResolver,
    ) {
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

        $queued = array_map(
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

        $activated = [];
        if ($sinceTimestamp !== null && $this->activeVersionResolver->hasVersioning()) {
            foreach ($this->activatedVersions($connection, $sinceTimestamp) as $row) {
                // change_id 0 es un centinela seguro SOLO porque la columna
                // se declara `identity="true"` en db_schema.xml, y MySQL
                // arranca AUTO_INCREMENT en 1: change_id real nunca es 0.
                $activated[] = [
                    'change_id' => 0,
                    'sku' => (string) $row['sku'],
                    'event' => 'save',
                    'changed_at' => gmdate('Y-m-d H:i:s', (int) $row['created_in']),
                ];
            }
        }

        // Las activaciones van PRIMERO: su change_id es 0, el más bajo
        // posible, y este endpoint promete los items en orden ascendente de
        // change_id. El lado Python (`delta_sync`) resuelve las colisiones de
        // un mismo SKU dentro de una página con "gana el último evento"
        // apoyándose exactamente en esa promesa; con las activaciones al final,
        // un SKU con un `delete` real de la cola y una activación en la misma
        // página terminaría refrescado en vez de borrado.
        $items = array_merge($activated, $queued);

        return WebApiEnvelope::wrap([
            'items' => $items,
            'last_change_id' => $lastChangeId,
        ]);
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
        // La ventana de versión activa (created_in <= ahora, updated_in >
        // ahora) es la MISMA que usa ProductReader, vía ActiveVersionResolver
        // — solo el "created_in > sinceTimestamp" de abajo es propio de esta
        // consulta: acota a lo que se activó DESPUÉS de la última lectura,
        // no a "toda la versión activa" en general.
        $select = $connection->select()
            ->from($entity, ['sku', 'created_in'])
            ->where('created_in > ?', $sinceTimestamp);
        $this->activeVersionResolver->applyToSelect($select);

        return $connection->fetchAll($select);
    }
}
