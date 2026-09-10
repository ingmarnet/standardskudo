<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Exception\LocalizedException;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;
use Standard\Skudo\Model\AttributeReader;
use Standard\Skudo\Model\Cursor;

/**
 * Cubre S0 Task A1: el primer de los dos endpoints que le dan un camino de
 * ingesta real a `attributes`/`attribute_options`/`option_labels` (hoy solo
 * alimentadas desde tests).
 *
 * Tres puntos concretos, verificados contra la instancia de referencia
 * (`/var/www/casanissei.com/v248`, solo lectura):
 *
 * 1. `declared_scope` viene de `catalog_eav_attribute.is_global`
 *    (1=global, 2=website, 0=store) — el tercer valor de procedencia que
 *    hoy no existe del lado Python. Un `is_global` fuera de {0,1,2} debe
 *    lanzar, no adivinar.
 * 2. Las etiquetas de una opción deben viajar TODAS, keyed por `store_id`,
 *    incluida la de `store_id = 0` (admin). Se verificó contra la instancia
 *    real que `eav_attribute_option_value.option_id = 216` tiene
 *    exactamente store_id 0 ("Exchange") y 1 ("Cambio por otro item") — un
 *    caso de dos store_ids CONSECUTIVOS DESDE CERO. Ese caso concreto es
 *    la razón por la que `labels` se emite como objeto (`(object)`, no un
 *    array PHP plano): un array PHP con claves 0 y 1 es indistinguible de
 *    una lista para `json_encode`, y se serializaría como `[...]` en vez
 *    de `{...}`, perdiendo la asociación store_id -> etiqueta que existe
 *    precisamente para no confundir "Negro" con "Preto" como dos opciones
 *    distintas. `testOptionLabelsStayAJsonObjectEvenWithConsecutiveStoreIdsFromZero()`
 *    reproduce ese caso real y falla si algún día `labels` vuelve a ser un
 *    array PHP plano.
 * 3. La paginación es por cursor sobre `attribute_id` (keyset), nunca por
 *    offset, y las opciones/attribute_set_ids solo se piden para los
 *    atributos de la página actual.
 *
 * Como en ProductReaderTest/SignalReaderTest, un `AttributeFakeSelect`
 * (abajo) registra y EVALÚA los where()/order()/limit() reales que arma
 * AttributeReader contra filas de fixture, dispatcheadas por la tabla
 * primaria de cada `from()`. Los `join()` son no-op: la fixture de cada
 * tabla ya viene "unida", igual que en los lectores hermanos.
 */
class AttributeReaderTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    private const CATALOG_PRODUCT_ENTITY_TYPE_ID = 4;

    public function testDeclaredScopeMapsIsGlobalOneToGlobal(): void
    {
        $result = $this->getPageWithAttributes([
            $this->attributeRow(10, isGlobal: 1),
        ]);

        $this->assertSame('global', $result['items'][0]['declared_scope']);
    }

    public function testDeclaredScopeMapsIsGlobalTwoToWebsite(): void
    {
        $result = $this->getPageWithAttributes([
            $this->attributeRow(10, isGlobal: 2),
        ]);

        $this->assertSame('website', $result['items'][0]['declared_scope']);
    }

    public function testDeclaredScopeMapsIsGlobalZeroToStore(): void
    {
        $result = $this->getPageWithAttributes([
            $this->attributeRow(10, isGlobal: 0),
        ]);

        $this->assertSame('store', $result['items'][0]['declared_scope']);
    }

    /**
     * Un `is_global` fuera de {0,1,2} es un error de datos que debe
     * surgir, no un default silencioso: inventar un cuarto valor de
     * procedencia contaminaría exactamente la distinción que el hallazgo
     * I1 existe para resolver.
     */
    public function testUnexpectedIsGlobalValueThrowsInsteadOfDefaulting(): void
    {
        $this->expectException(LocalizedException::class);

        $this->getPageWithAttributes([
            $this->attributeRow(10, isGlobal: 9),
        ]);
    }

    /**
     * El punto central del brief: un option_id con etiquetas en las
     * store views 0 (admin), 1 y 3 debe devolver LAS TRES, no solo una.
     * Devolver una sola destruiría la identidad que la Task 5 protege
     * (una "Negro"/"Preto" tratada como dos opciones a "consolidar").
     */
    public function testOptionLabelsIncludeAllThreeStoreViewsIncludingAdminDefault(): void
    {
        $result = $this->getPageWithAttributesAndOptions(
            attributes: [$this->attributeRow(50)],
            options: [['option_id' => 500, 'attribute_id' => 50, 'sort_order' => 1]],
            optionValues: [
                ['option_id' => 500, 'store_id' => 0, 'value' => 'Negro'],
                ['option_id' => 500, 'store_id' => 1, 'value' => 'Negro PY'],
                ['option_id' => 500, 'store_id' => 3, 'value' => 'Preto BR'],
            ],
        );

        $options = $result['items'][0]['options'];
        $this->assertCount(1, $options);
        $this->assertSame(500, $options[0]['option_id']);
        $this->assertSame(
            [0 => 'Negro', 1 => 'Negro PY', 3 => 'Preto BR'],
            (array) $options[0]['labels']
        );
    }

    /**
     * Caso real verificado en la instancia de referencia (option_id 216:
     * store_id 0 y 1, consecutivos desde cero). Con un array PHP plano
     * `[0 => 'Exchange', 1 => 'Cambio por otro item']`,
     * `array_is_list()` es true y `json_encode` produce `["Exchange",
     * "Cambio por otro item"]` — una LISTA que pierde qué etiqueta es de
     * qué store view. Esta prueba fuerza el objeto (`is_object`) y
     * verifica que el primer carácter de su JSON sea `{`, no `[`: si
     * `labels` volviera a ser un array PHP, esta prueba falla con datos
     * reales, no con una suposición teórica.
     */
    public function testOptionLabelsStayAJsonObjectEvenWithConsecutiveStoreIdsFromZero(): void
    {
        $result = $this->getPageWithAttributesAndOptions(
            attributes: [$this->attributeRow(60)],
            options: [['option_id' => 600, 'attribute_id' => 60, 'sort_order' => 1]],
            optionValues: [
                ['option_id' => 600, 'store_id' => 0, 'value' => 'Exchange'],
                ['option_id' => 600, 'store_id' => 1, 'value' => 'Cambio por otro item'],
            ],
        );

        $labels = $result['items'][0]['options'][0]['labels'];

        $this->assertIsObject($labels, 'labels debe ser un objeto, no un array PHP plano indistinguible de una lista');
        $this->assertSame('{', substr(json_encode($labels), 0, 1), 'un array con claves 0,1 se serializaría como lista, no como objeto');
        $this->assertSame([0 => 'Exchange', 1 => 'Cambio por otro item'], (array) $labels);
    }

    /**
     * Regla de paginación: por cursor (keyset) sobre attribute_id, nunca
     * por offset. El fixture tiene tres atributos (100, 200, 300); pedir
     * limit=2 debe traer 100 y 200 (en ese orden) con next_cursor
     * apuntando a 200, y pedir la página siguiente con ese cursor debe
     * traer SOLO 300 (attribute_id > 200), no repetir ni saltar filas.
     */
    public function testGetPagePaginatesByAttributeIdCursorNotByOffset(): void
    {
        $selects = [];
        $reader = $this->makeReader(
            fixtures: [
                'eav_entity_type' => $this->entityTypeFixture(),
                'eav_attribute' => [
                    $this->attributeRow(100),
                    $this->attributeRow(200),
                    $this->attributeRow(300),
                ],
            ],
            selects: $selects,
        );

        $firstPage = $this->payloadOf($reader->getPage(limit: 2));

        $this->assertSame(['attr_100', 'attr_200'], array_column($firstPage['items'], 'code'));
        $this->assertNotNull($firstPage['next_cursor']);
        $this->assertSame(200, (new Cursor())->decode($firstPage['next_cursor']));

        $secondPage = $this->payloadOf($reader->getPage(limit: 2, cursor: $firstPage['next_cursor']));

        $this->assertSame(['attr_300'], array_column($secondPage['items'], 'code'));
        $this->assertNull($secondPage['next_cursor'], 'una página corta (menos filas que el límite) no debe pedir otra página más');
    }

    /**
     * `attribute_set_ids` sale de `eav_entity_attribute` y es lo que
     * permite distinguir "no aplica" de "aplica y está vacío" (eje 3 del
     * spec). Debe traerse SOLO para los atributos de la página actual: el
     * atributo 999 (fuera de esta página) nunca debe filtrarse en la
     * respuesta de 100/200, aunque exista en la misma tabla.
     */
    public function testAttributeSetIdsComeFromEavEntityAttributeOnlyForAttributesOnThisPage(): void
    {
        $selects = [];
        $reader = $this->makeReader(
            fixtures: [
                'eav_entity_type' => $this->entityTypeFixture(),
                'eav_attribute' => [
                    $this->attributeRow(100),
                    $this->attributeRow(200),
                ],
                'eav_entity_attribute' => [
                    ['attribute_id' => 100, 'attribute_set_id' => 7],
                    ['attribute_id' => 100, 'attribute_set_id' => 4],
                    ['attribute_id' => 200, 'attribute_set_id' => 55],
                    // Decoy: un atributo que NO está en esta página. Si el
                    // filtro `attribute_id IN (?)` se rompiera y trajera
                    // la tabla entera, este valor contaminaría alguna de
                    // las dos listas de arriba.
                    ['attribute_id' => 999, 'attribute_set_id' => 12345],
                ],
            ],
            selects: $selects,
        );

        $result = $this->payloadOf($reader->getPage(limit: 10));

        $bySku = array_column($result['items'], 'attribute_set_ids', 'code');
        $this->assertSame([4, 7], $bySku['attr_100']);
        $this->assertSame([55], $bySku['attr_200']);
    }

    /**
     * "Restrict to entity type catalog_product": un atributo de otro
     * entity type (customer, entity_type_id 1 en este fixture) no debe
     * aparecer, aunque su attribute_id caiga dentro del rango del cursor.
     */
    public function testOnlyAttributesOfCatalogProductEntityTypeAreReturned(): void
    {
        $result = $this->getPageWithAttributes([
            $this->attributeRow(70, entityTypeId: 1),
            $this->attributeRow(80, entityTypeId: self::CATALOG_PRODUCT_ENTITY_TYPE_ID),
        ]);

        $this->assertCount(1, $result['items']);
        $this->assertSame('attr_80', $result['items'][0]['code']);
    }

    public function testFrontendInputIsFilterableAndIsRequiredArePassedThrough(): void
    {
        $result = $this->getPageWithAttributes([
            $this->attributeRow(10, frontendInput: 'select', isRequired: 1, isFilterable: 1),
        ]);

        $item = $result['items'][0];
        $this->assertSame('select', $item['frontend_input']);
        $this->assertTrue($item['is_required']);
        $this->assertTrue($item['is_filterable']);
    }

    /** @param mixed[] $attributeRows */
    private function getPageWithAttributes(array $attributeRows): array
    {
        $selects = [];
        $reader = $this->makeReader(
            fixtures: [
                'eav_entity_type' => $this->entityTypeFixture(),
                'eav_attribute' => $attributeRows,
            ],
            selects: $selects,
        );

        return $this->payloadOf($reader->getPage(limit: 10));
    }

    /**
     * @param mixed[] $attributes
     * @param mixed[] $options
     * @param mixed[] $optionValues
     */
    private function getPageWithAttributesAndOptions(array $attributes, array $options, array $optionValues): array
    {
        $selects = [];
        $reader = $this->makeReader(
            fixtures: [
                'eav_entity_type' => $this->entityTypeFixture(),
                'eav_attribute' => $attributes,
                'eav_attribute_option' => $options,
                'eav_attribute_option_value' => $optionValues,
            ],
            selects: $selects,
        );

        return $this->payloadOf($reader->getPage(limit: 10));
    }

    /** @return mixed[] */
    private function entityTypeFixture(): array
    {
        return [
            ['entity_type_code' => 'catalog_product', 'entity_type_id' => self::CATALOG_PRODUCT_ENTITY_TYPE_ID],
        ];
    }

    /** @return mixed[] */
    private function attributeRow(
        int $attributeId,
        int $isGlobal = 1,
        int $entityTypeId = self::CATALOG_PRODUCT_ENTITY_TYPE_ID,
        string $frontendInput = 'text',
        int $isRequired = 0,
        int $isFilterable = 0,
    ): array {
        return [
            'attribute_id' => $attributeId,
            'entity_type_id' => $entityTypeId,
            'attribute_code' => "attr_{$attributeId}",
            'frontend_label' => "Label {$attributeId}",
            'frontend_input' => $frontendInput,
            'is_required' => $isRequired,
            'is_global' => $isGlobal,
            'is_filterable' => $isFilterable,
        ];
    }

    /**
     * @param array<string, mixed[]> $fixtures Filas de fetchAll()/fetchOne()
     *     por tabla primaria (la de `from()`), ya "unidas" (ver docblock
     *     de la clase).
     * @param list<AttributeFakeSelect> $selects
     */
    private function makeReader(array $fixtures, array &$selects): AttributeReader
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(static function () use (&$selects): AttributeFakeSelect {
            $select = new AttributeFakeSelect();
            $selects[] = $select;
            return $select;
        });
        $connection->method('fetchAll')->willReturnCallback(
            fn (AttributeFakeSelect $select): array => $this->evaluateSelect($select, $fixtures[$select->table] ?? [])
        );
        $connection->method('fetchOne')->willReturnCallback(
            function (AttributeFakeSelect $select) use ($fixtures) {
                $rows = $this->evaluateSelect($select, $fixtures[$select->table] ?? []);
                if ($rows === []) {
                    return false;
                }
                $first = array_values($rows[0]);
                return $first[0] ?? false;
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new AttributeReader($resource, new Cursor());
    }

    /**
     * Ejecuta (de verdad) los where()/order()/limit() que
     * AttributeReader armó, contra las filas de fixture de esa tabla —
     * igual que ProductReaderTest/DeltaReaderTest, para que un filtro
     * borrado o mal escrito falle con datos concretos.
     *
     * @param mixed[] $fixtureRows
     * @return mixed[]
     */
    private function evaluateSelect(AttributeFakeSelect $select, array $fixtureRows): array
    {
        $rows = $fixtureRows;
        foreach ($select->wheres as $where) {
            $rows = array_values(array_filter(
                $rows,
                fn (array $row): bool => $this->conditionMatches($row, $where['cond'], $where['value'])
            ));
        }

        if ($select->orderSpec !== null
            && preg_match('/^(?:\w+\.)?(\w+)\s+(ASC|DESC)$/i', $select->orderSpec, $m) === 1
        ) {
            $field = $m[1];
            $direction = strtoupper($m[2]);
            usort($rows, static fn (array $a, array $b): int => $direction === 'ASC'
                ? $a[$field] <=> $b[$field]
                : $b[$field] <=> $a[$field]);
        }

        if ($select->limitCount !== null) {
            $rows = array_slice($rows, 0, $select->limitCount);
        }

        return array_map(fn (array $row): array => $this->projectColumns($row, $select->columns), $rows);
    }

    private function conditionMatches(array $row, string $cond, mixed $value): bool
    {
        if (preg_match('/^(?:\w+\.)?(\w+)\s*(>=|<=|>|<|=)\s*\?$/', $cond, $m) === 1) {
            return $this->compare($row[$m[1]] ?? null, $m[2], $value);
        }
        if (preg_match('/^(?:\w+\.)?(\w+) IN \(\?\)$/', $cond, $m) === 1) {
            return in_array($row[$m[1]] ?? null, (array) $value, false);
        }

        throw new \RuntimeException("condición de prueba no reconocida: {$cond}");
    }

    private function compare(mixed $actual, string $op, mixed $expected): bool
    {
        return match ($op) {
            '>' => $actual > $expected,
            '>=' => $actual >= $expected,
            '<' => $actual < $expected,
            '<=' => $actual <= $expected,
            '=' => $actual == $expected,
        };
    }

    /**
     * @param mixed[] $row
     * @param array<int|string, string> $columns
     * @return mixed[]
     */
    private function projectColumns(array $row, array $columns): array
    {
        $out = [];
        foreach ($columns as $alias => $expr) {
            $field = str_contains($expr, '.') ? explode('.', $expr, 2)[1] : $expr;
            $out[is_int($alias) ? $field : $alias] = $row[$field] ?? null;
        }
        return $out;
    }
}

/**
 * Doble de prueba de `Magento\Framework\DB\Select`: registra la tabla, las
 * columnas, los `where()` encadenados y el `order()`/`limit()` pedidos,
 * sin tocar ninguna base de datos. Ver el docblock de la clase homónima en
 * ProductReaderTest para el razonamiento de extender la clase real.
 */
final class AttributeFakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var array<int|string, string> */
    public array $columns = [];

    /** @var list<array{cond: string, value: mixed}> */
    public array $wheres = [];

    public ?string $orderSpec = null;

    public ?int $limitCount = null;

    public function __construct()
    {
    }

    /**
     * @param mixed $tables
     * @param string|array<int|string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        $this->table = (string) array_values((array) $tables)[0];
        $this->columns = is_array($columns) ? $columns : [$columns];
        return $this;
    }

    /**
     * A diferencia de ProductReaderTest/SignalReaderTest (donde los join()
     * son no-op porque las columnas EAV se leen en consultas separadas),
     * AttributeReader::baseAttributeSelect() pide columnas de la tabla
     * unida (`is_global`/`is_filterable`) en la MISMA consulta, así que
     * este doble sí debe agregarlas a la proyección — igual que
     * `Magento\Framework\DB\Select::join()` real, que también fusiona sus
     * columnas a la lista total de columnas seleccionadas.
     *
     * @param mixed $tables
     * @param string|array<int|string, string> $columns
     * @param mixed $schema
     */
    public function join($tables, $cond, $columns = '*', $schema = null): self
    {
        if ($columns !== '*') {
            $this->columns = array_merge($this->columns, is_array($columns) ? $columns : [$columns]);
        }
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[] = ['cond' => $cond, 'value' => $value];
        return $this;
    }

    public function order($spec): self
    {
        $this->orderSpec = is_array($spec) ? implode(',', $spec) : (string) $spec;
        return $this;
    }

    public function limit($count = null, $offset = null): self
    {
        $this->limitCount = $count !== null ? (int) $count : null;
        return $this;
    }
}
