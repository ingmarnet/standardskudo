<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\DB\Select;
use Magento\Framework\Exception\LocalizedException;
use Standard\Skudo\Api\AttributeReaderInterface;

/**
 * S0 Task A1: primero de los dos endpoints que le dan a
 * `attributes`/`attribute_options`/`option_labels` (espejo) un camino de
 * ingesta real. Antes de esta clase, las funciones de upsert de esas tres
 * tablas solo se llamaban desde tests: dos de los cuatro criterios de
 * aceptación del sub-proyecto no podían pasar contra un espejo real.
 *
 * Esta clase NO reimplementa ninguna detección de esquema: a diferencia de
 * ProductReader/DeltaReader/SignalReader/ChecksumReader, no consume
 * EntityKeyResolver ni ActiveVersionResolver. Se verificó contra la
 * instancia de referencia (solo lectura) que ninguna de las tablas que este
 * lector toca — `eav_attribute`, `catalog_eav_attribute`,
 * `eav_entity_attribute`, `eav_attribute_option`,
 * `eav_attribute_option_value` — tiene columnas `row_id`/`entity_id` ni
 * `created_in`/`updated_in`: esas dos preguntas de esquema son propias de
 * `catalog_product_entity` (los DATOS de producto, versionados por
 * Magento_Staging), no de las DEFINICIONES de atributo/opción, que no se
 * versionan. Inyectar cualquiera de los dos resolvers acá sería la
 * reimplementación al revés que este proyecto existe para evitar: usar una
 * pieza compartida donde no responde ninguna pregunta real.
 *
 * Tres puntos que deciden si esto sirve (ver AttributeReaderTest):
 *
 * 1. `declared_scope` se deriva de `catalog_eav_attribute.is_global`
 *    (1=global, 2=website, 0=store) — nunca a partir de la edición ni de
 *    un valor por defecto. Un `is_global` fuera de {0,1,2} es un error de
 *    datos que debe surgir (LocalizedException), no colapsarse a un cuarto
 *    valor inventado.
 * 2. Las etiquetas de una opción se devuelven TODAS, keyed por `store_id`
 *    (incluida `store_id = 0`, la admin), como OBJETO (`(object)`), nunca
 *    como array PHP plano. Se verificó contra la instancia de referencia
 *    que `eav_attribute_option_value.option_id = 216` tiene exactamente
 *    dos filas, store_id 0 ("Exchange") y 1 ("Cambio por otro item") — dos
 *    claves consecutivas desde cero. Un array PHP `[0 => ..., 1 => ...]`
 *    es indistinguible de una lista para `array_is_list()`, y
 *    `json_encode()` lo serializaría como `[...]`, perdiendo la asociación
 *    store_id -> etiqueta que existe precisamente para que "Negro"
 *    (PY) y "Preto" (BR) sigan siendo UNA opción con dos labels, no dos
 *    opciones a "consolidar" — la corrección más destructiva que este
 *    producto podría hacer. Forzar el objeto es lo único que garantiza,
 *    del lado de PHP, que el JSON de salida sea `{...}` sin importar qué
 *    store_ids concretos tenga cada opción.
 * 3. `attribute_set_ids` sale de `eav_entity_attribute` y es lo que le
 *    permite al lado Python distinguir "no aplica a este set" de "aplica y
 *    está vacío" (eje 3 del spec) — confundir ambos es exactamente el
 *    fallo que el spec entero existe para prevenir.
 *
 * Paginación por cursor (keyset) sobre `attribute_id`, nunca offset — misma
 * razón que ProductReader: con 1.066 atributos de este entity type y
 * decenas de miles de opciones, las opciones/attribute_set_ids solo se
 * piden para los atributos de la página actual, nunca para el catálogo de
 * atributos completo.
 */
class AttributeReader implements AttributeReaderInterface
{
    private const MAX_LIMIT = 1000;

    /**
     * El catálogo EAV también tiene atributos de customer, category y
     * address: mirrorearlos sería scope creep y ruido ajeno al spec, que
     * solo habla de calidad de PRODUCTOS.
     */
    private const ENTITY_TYPE_CODE = 'catalog_product';

    private const SCOPE_MAP = [
        '1' => 'global',
        '2' => 'website',
        '0' => 'store',
    ];

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly Cursor $cursor,
    ) {
    }

    public function getPage(int $limit = 500, ?string $cursor = null): array
    {
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $after = $this->cursor->decode($cursor);
        $connection = $this->resource->getConnection();

        $entityTypeId = $this->resolveEntityTypeId($connection);

        $select = $this->baseAttributeSelect($connection, $entityTypeId)
            ->where('a.attribute_id > ?', $after)
            ->order('a.attribute_id ASC')
            ->limit($limit);

        $rows = $connection->fetchAll($select);
        if ($rows === []) {
            return WebApiEnvelope::wrap(['items' => [], 'next_cursor' => null]);
        }

        $attributeIds = array_map(static fn (array $row): int => (int) $row['attribute_id'], $rows);
        $attributeSetIds = $this->attributeSetIds($connection, $attributeIds);
        $optionsByAttribute = $this->optionsByAttribute($connection, $attributeIds);

        $items = [];
        foreach ($rows as $row) {
            $attributeId = (int) $row['attribute_id'];
            $items[] = [
                'code' => (string) $row['code'],
                'label' => (string) $row['label'],
                'frontend_input' => (string) $row['frontend_input'],
                'declared_scope' => $this->declaredScope($row['is_global']),
                'is_filterable' => (bool) $row['is_filterable'],
                'is_required' => (bool) $row['is_required'],
                'attribute_set_ids' => $attributeSetIds[$attributeId] ?? [],
                'options' => $optionsByAttribute[$attributeId] ?? [],
            ];
        }

        return WebApiEnvelope::wrap([
            'items' => $items,
            'next_cursor' => count($rows) < $limit
                ? null
                : $this->cursor->encode($attributeIds[array_key_last($attributeIds)]),
        ]);
    }

    /**
     * `entity_type_id` de `catalog_product` no está hardcodeado: se
     * resuelve en runtime contra `eav_entity_type`, porque asumir un id
     * fijo (típicamente 4) sería frágil ante cualquier instancia donde el
     * EAV se haya sembrado en otro orden.
     */
    private function resolveEntityTypeId(AdapterInterface $connection): int
    {
        $select = $connection->select()
            ->from($this->resource->getTableName('eav_entity_type'), ['entity_type_id'])
            ->where('entity_type_code = ?', self::ENTITY_TYPE_CODE);

        $entityTypeId = $connection->fetchOne($select);
        if ($entityTypeId === false) {
            throw new LocalizedException(__(
                'no se encontró entity_type_id para "%1" en eav_entity_type',
                self::ENTITY_TYPE_CODE
            ));
        }

        return (int) $entityTypeId;
    }

    private function baseAttributeSelect(AdapterInterface $connection, int $entityTypeId): Select
    {
        $attribute = $this->resource->getTableName('eav_attribute');
        $catalogAttribute = $this->resource->getTableName('catalog_eav_attribute');

        return $connection->select()
            ->from(['a' => $attribute], [
                'attribute_id' => 'a.attribute_id',
                'code' => 'a.attribute_code',
                'label' => 'a.frontend_label',
                'frontend_input' => 'a.frontend_input',
                'is_required' => 'a.is_required',
            ])
            ->join(
                ['ca' => $catalogAttribute],
                'ca.attribute_id = a.attribute_id',
                ['is_global' => 'ca.is_global', 'is_filterable' => 'ca.is_filterable']
            )
            ->where('a.entity_type_id = ?', $entityTypeId);
    }

    /**
     * Ruling 1 (Task A1): un `is_global` fuera de {0,1,2} lanza. Inventar
     * un cuarto valor de procedencia (o quedarse con el primero de la
     * lista por default) sería exactamente el tipo de "corrección" que el
     * hallazgo I1 no puede permitirse: procedencia de scope equivocada
     * envenena cualquier hallazgo que dependa de ella.
     */
    private function declaredScope(mixed $isGlobal): string
    {
        $key = (string) $isGlobal;
        if (!isset(self::SCOPE_MAP[$key])) {
            throw new LocalizedException(__(
                'is_global inesperado en catalog_eav_attribute: "%1" (se esperaba 0, 1 o 2)',
                $key
            ));
        }

        return self::SCOPE_MAP[$key];
    }

    /**
     * @param int[] $attributeIds
     * @return array<int, int[]>
     */
    private function attributeSetIds(AdapterInterface $connection, array $attributeIds): array
    {
        $table = $this->resource->getTableName('eav_entity_attribute');
        $select = $connection->select()
            ->from(
                ['ea' => $table],
                ['attribute_id' => 'ea.attribute_id', 'attribute_set_id' => 'ea.attribute_set_id']
            )
            // Solo los atributos de la página actual: con 1.066 atributos
            // y potencialmente cientos de attribute sets, traer la tabla
            // entera por cada página sería exactamente el costo que la
            // paginación por cursor existe para evitar.
            ->where('ea.attribute_id IN (?)', $attributeIds);

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(int) $row['attribute_id']][] = (int) $row['attribute_set_id'];
        }
        foreach (array_keys($out) as $attributeId) {
            sort($out[$attributeId]);
        }

        return $out;
    }

    /**
     * Opciones (y TODAS sus etiquetas por store view) para los atributos
     * de la página actual, en dos pasadas: primero qué option_ids existen
     * por atributo, después qué etiquetas tiene cada uno. Separado en dos
     * consultas (en vez de un join) porque `eav_attribute_option_value`
     * tiene una fila POR STORE VIEW por opción — un join las multiplicaría
     * contra las columnas de `eav_attribute_option`, que no cambian por
     * store view.
     *
     * @param int[] $attributeIds
     * @return array<int, mixed[]>
     */
    private function optionsByAttribute(AdapterInterface $connection, array $attributeIds): array
    {
        $optionTable = $this->resource->getTableName('eav_attribute_option');
        $optionSelect = $connection->select()
            ->from(['o' => $optionTable], ['option_id' => 'o.option_id', 'attribute_id' => 'o.attribute_id'])
            ->where('o.attribute_id IN (?)', $attributeIds)
            ->order('o.sort_order ASC');

        $optionRows = $connection->fetchAll($optionSelect);
        if ($optionRows === []) {
            return [];
        }

        $optionIds = array_map(static fn (array $row): int => (int) $row['option_id'], $optionRows);

        $valueTable = $this->resource->getTableName('eav_attribute_option_value');
        $valueSelect = $connection->select()
            ->from(
                ['v' => $valueTable],
                ['option_id' => 'v.option_id', 'store_id' => 'v.store_id', 'value' => 'v.value']
            )
            // Todas las filas de todos los store views del option_id — la
            // identidad de una opción es su option_id, nunca su etiqueta
            // (ver Model\EntityKeyResolver para el precedente de esa
            // misma regla aplicada a productos). Devolver una sola
            // etiqueta destruiría esa identidad.
            ->where('v.option_id IN (?)', $optionIds);

        $labelsByOption = [];
        foreach ($connection->fetchAll($valueSelect) as $row) {
            $optionId = (int) $row['option_id'];
            $labelsByOption[$optionId][(int) $row['store_id']] = $row['value'] === null
                ? null
                : (string) $row['value'];
        }

        $out = [];
        foreach ($optionRows as $row) {
            $attributeId = (int) $row['attribute_id'];
            $optionId = (int) $row['option_id'];
            $out[$attributeId][] = [
                'option_id' => $optionId,
                // Objeto, no array PHP: ver Ruling 2 de la clase. Un mapa
                // store_id -> label con claves 0 y 1 (caso real verificado:
                // option_id 216) es indistinguible de una lista para
                // json_encode() si se deja como array PHP.
                'labels' => (object) ($labelsByOption[$optionId] ?? []),
            ];
        }

        return $out;
    }
}
