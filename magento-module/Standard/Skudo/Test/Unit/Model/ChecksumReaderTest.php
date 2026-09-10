<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;
use Standard\Skudo\Model\ActiveVersionResolver;
use Standard\Skudo\Model\ChecksumReader;

/**
 * S0 Task 13: el lado Magento del endpoint de reconciliación. Estas pruebas
 * cubren tres cosas que, si se rompen, hacen que `reconcile()` (lado Python,
 * `src/skudo/ingest/reconcile.py`) reporte deriva PERMANENTE — un falso
 * positivo del que un re-sync completo nunca podría recuperarse, porque el
 * digest recalculado seguiría sin coincidir:
 *
 *   1. El digest de un conjunto de SKUs conocido tiene que coincidir BYTE A
 *      BYTE con el que produce `sku_digest()` en Python para el mismo
 *      conjunto (ver test abajo: el hash esperado se calculó una sola vez,
 *      de forma independiente, con `hashlib.sha256` en Python y con
 *      `hash('sha256', ...)` en PHP sobre los mismos tres SKUs, y ambos
 *      coincidieron — ver el reporte de esta tarea para el cálculo).
 *   2. El orden final NO puede depender del orden en que la fila de fixture
 *      (o, en producción, el motor de base de datos) las entrega: se ordena
 *      SIEMPRE en PHP con `sort($skus, SORT_STRING)`, nunca confiando en un
 *      `ORDER BY` SQL (ver ChecksumReader::getChecksums() para el porqué:
 *      `utf8mb4_general_ci` ordena sin distinguir mayúsculas/acentos, que no
 *      es el orden de puntos de código que usa Python).
 *   3. Solo la versión activa entra al digest (Ruling 3): con
 *      Magento_Staging, `catalog_product_entity` tiene una fila por VERSIÓN,
 *      no por producto, así que sin este filtro el digest tendría más filas
 *      que el espejo (que guarda una fila por producto) y jamás coincidiría.
 */
class ChecksumReaderTest extends TestCase
{
    use UnwrapsWebApiEnvelope;

    /**
     * Calculado independientemente en Python:
     *   hashlib.sha256("\n".join(sorted(["SKU-A","SKU-B","SKU-C"])).encode("utf-8")).hexdigest()
     * y en PHP:
     *   hash('sha256', implode("\n", (function () {
     *       $s = ["SKU-A","SKU-B","SKU-C"]; sort($s, SORT_STRING); return $s;
     *   })()))
     * Ambos producen el mismo valor. Es la comprobación cruzada de lenguajes
     * más cercana posible sin una instancia Magento viva (esa la hace la
     * Task 14 contra la instancia de referencia).
     */
    private const EXPECTED_DIGEST = '2f4b9f61cc9b9f74f016af48a26e7e3e435fd778de2e359a8c5eef342e46aaff';

    public function testDigestMatchesThePythonAlgorithmForAKnownSkuSet(): void
    {
        $reader = $this->makeReader(hasVersioning: false, entityRows: [
            $this->entityRow('SKU-A'),
            $this->entityRow('SKU-B'),
            $this->entityRow('SKU-C'),
        ]);

        $result = $this->payloadOf($reader->getChecksums(storeId: 1));

        $this->assertSame(3, $result['product_count']);
        $this->assertSame(self::EXPECTED_DIGEST, $result['sku_digest']);
    }

    /**
     * Las filas llegan de la fixture en un orden que NO es el alfabético
     * (ni tampoco su inverso), imitando lo que haría una base de datos real
     * bajo una colación case/accent-insensitive. Si ChecksumReader confiara
     * en el orden de la consulta (o en un `ORDER BY sku` SQL) en vez de
     * ordenar en PHP con SORT_STRING, este digest no coincidiría con
     * EXPECTED_DIGEST.
     */
    public function testDigestIsComputedFromAPhpSortNotQueryOrder(): void
    {
        $reader = $this->makeReader(hasVersioning: false, entityRows: [
            $this->entityRow('SKU-C'),
            $this->entityRow('SKU-A'),
            $this->entityRow('SKU-B'),
        ]);

        $result = $this->payloadOf($reader->getChecksums(storeId: 1));

        $this->assertSame(self::EXPECTED_DIGEST, $result['sku_digest']);
    }

    /**
     * Ruling 2: storeId se acepta por simetría de interfaz pero NO filtra.
     * Se pide con dos valores distintos de storeId contra el mismo fixture
     * y se exige el mismo resultado exacto en ambos: si alguien "corrigiera"
     * esto para filtrar por store view, el conteo y el digest ya no
     * coincidirían con mirror_count/sku_digest del espejo (que guarda una
     * fila por producto por store view, no una vista filtrada), y
     * `reconcile()` exigiría un re-sync completo para siempre.
     */
    public function testStoreIdDoesNotFilterTheResult(): void
    {
        $entityRows = [$this->entityRow('SKU-A'), $this->entityRow('SKU-B')];

        $resultForStoreOne = $this->makeReader(hasVersioning: false, entityRows: $entityRows)
            ->getChecksums(storeId: 1);
        $resultForStoreTwo = $this->makeReader(hasVersioning: false, entityRows: $entityRows)
            ->getChecksums(storeId: 2);

        $this->assertSame($resultForStoreOne, $resultForStoreTwo);
    }

    /**
     * Reproduce el mismo escenario que ProductReaderTest (SKU con dos
     * versiones bajo Magento_Staging): sin el filtro de ActiveVersionResolver,
     * el digest incluiría la fila vieja Y la nueva del mismo SKU, en vez de
     * una sola por producto, y jamás coincidiría con el espejo.
     */
    public function testOnlyTheActiveVersionEntersTheDigestWhenVersioningIsPresent(): void
    {
        $entityRows = [
            // Versión vieja, cerrada en el pasado (updated_in en 2001).
            [
                'sku' => 'SKU-A', 'created_in' => 1, 'updated_in' => 1_000_000_000,
            ],
            // Versión activa (centinela "sin fin").
            [
                'sku' => 'SKU-A', 'created_in' => 1_000_000_000, 'updated_in' => 2_147_483_647,
            ],
            [
                'sku' => 'SKU-B', 'created_in' => 1, 'updated_in' => 2_147_483_647,
            ],
        ];

        $reader = $this->makeReader(hasVersioning: true, entityRows: $entityRows);

        $result = $this->payloadOf($reader->getChecksums(storeId: 1));

        // Solo dos SKUs activos (SKU-A una vez, SKU-B una vez), no tres filas.
        $this->assertSame(2, $result['product_count']);
        $expected = hash('sha256', implode("\n", ['SKU-A', 'SKU-B']));
        $this->assertSame($expected, $result['sku_digest']);
    }

    public function testNoActiveVersionFilterIsAddedWhenVersioningColumnsAreAbsent(): void
    {
        $selects = [];
        $reader = $this->makeReader(hasVersioning: false, entityRows: [$this->entityRow('SKU-A')], selects: $selects);

        $this->payloadOf($reader->getChecksums(storeId: 1));

        // Sin columnas de versionado, ActiveVersionResolver no agrega
        // ningún where(): la consulta queda vacía de condiciones (no solo
        // "sin created_in/updated_in", que pasaría trivialmente si el
        // where() nunca se ejecutara).
        $this->assertSame([], $selects[0]->wheres);
    }

    /**
     * @param mixed[] $entityRows
     * @return mixed[]
     */
    private function entityRow(string $sku): array
    {
        return ['sku' => $sku];
    }

    /**
     * @param mixed[] $entityRows
     * @param list<ChecksumFakeSelect> $selects
     */
    private function makeReader(bool $hasVersioning, array $entityRows, array &$selects = []): ChecksumReader
    {
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('tableColumnExists')->willReturnCallback(
            static fn (string $table, string $column): bool => match ($column) {
                'created_in', 'updated_in' => $hasVersioning,
                default => false,
            }
        );
        $connection->method('select')->willReturnCallback(static function () use (&$selects): ChecksumFakeSelect {
            $select = new ChecksumFakeSelect();
            $selects[] = $select;
            return $select;
        });
        $connection->method('fetchCol')->willReturnCallback(
            fn (ChecksumFakeSelect $select): array => $this->evaluateEntitySelect($select, $entityRows)
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new ChecksumReader($resource, new ActiveVersionResolver($resource));
    }

    /**
     * Aplica los `where()` reales que armó ChecksumReader contra las filas
     * de fixture y devuelve solo la columna `sku`, en el ORDEN en que las
     * filas quedaron en el fixture (deliberadamente no ordenado
     * alfabéticamente en varios tests, para probar que el orden de la
     * consulta es irrelevante).
     *
     * @param mixed[] $entityRows
     * @return string[]
     */
    private function evaluateEntitySelect(ChecksumFakeSelect $select, array $entityRows): array
    {
        $rows = $entityRows;
        foreach ($select->wheres as $where) {
            $rows = array_values(array_filter(
                $rows,
                fn (array $row): bool => $this->conditionMatches($row, $where['cond'])
            ));
        }

        return array_map(static fn (array $row): string => $row['sku'], $rows);
    }

    private function conditionMatches(array $row, string $cond): bool
    {
        if (preg_match('/^e\.(\w+)\s*(<=|>=|<|>)\s*UNIX_TIMESTAMP\(\)$/', $cond, $m) === 1) {
            $field = $m[1];
            $op = $m[2];
            $actual = $row[$field] ?? null;
            $expected = time();
            return match ($op) {
                '<=' => $actual <= $expected,
                '>=' => $actual >= $expected,
                '<' => $actual < $expected,
                '>' => $actual > $expected,
            };
        }

        throw new \RuntimeException("condición de prueba no reconocida: {$cond}");
    }
}

/**
 * Doble de prueba mínimo de `Magento\Framework\DB\Select`, siguiendo el
 * mismo patrón que `FakeSelect` en ProductReaderTest y `DeltaFakeSelect` en
 * DeltaReaderTest: solo registra lo que ChecksumReader realmente usa
 * (`from()`/`where()`), sin `order()` — a propósito, porque ChecksumReader
 * nunca debe pedirle un ORDER BY a SQL (ver Ruling 1 del brief).
 */
final class ChecksumFakeSelect extends \Magento\Framework\DB\Select
{
    public string $table = '';

    /** @var list<array{cond: string, value: mixed}> */
    public array $wheres = [];

    public function __construct()
    {
    }

    /**
     * @param mixed $tables
     * @param string|array<string, string> $columns
     * @param mixed $schema
     */
    public function from($tables, $columns = '*', $schema = null): self
    {
        $this->table = (string) array_values((array) $tables)[0];
        return $this;
    }

    public function where($cond, $value = null, $type = null): self
    {
        $this->wheres[] = ['cond' => $cond, 'value' => $value];
        return $this;
    }

    /**
     * Deliberadamente sin implementación real: si ChecksumReader llamara a
     * `order()`, este doble lo registraría igual (encadenado fluido), pero
     * ninguna prueba de este archivo lo evalúa — precisamente porque
     * ChecksumReader no debe depender de un ORDER BY SQL.
     */
    public function order($spec): self
    {
        return $this;
    }
}
