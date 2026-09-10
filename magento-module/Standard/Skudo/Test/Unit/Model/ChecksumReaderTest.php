<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Test\Unit\WebApi\UnwrapsWebApiEnvelope;
use Standard\Skudo\Model\ChecksumReader;
use Standard\Skudo\Model\ContentDigest;

/**
 * S0 Task 13 + H1: el lado Magento del endpoint de reconciliación. Estas
 * pruebas cubren las cosas que, si se rompen, hacen que `reconcile()` (lado
 * Python, `src/skudo/ingest/reconcile.py`) reporte deriva PERMANENTE — un
 * falso positivo del que un re-sync completo nunca podría recuperarse,
 * porque el digest recalculado seguiría sin coincidir:
 *
 *   1. El digest de un conjunto de SKUs conocido tiene que coincidir BYTE A
 *      BYTE con el que produce `sku_digest()` en Python para el mismo
 *      conjunto.
 *   2. El orden final NO puede depender del orden en que la fila de fixture
 *      (o, en producción, el motor de base de datos) las entrega: se ordena
 *      SIEMPRE en PHP con `sort($skus, SORT_STRING)`, nunca confiando en un
 *      `ORDER BY` SQL (ver ChecksumReader::getChecksums() para el porqué:
 *      `utf8mb4_general_ci` ordena sin distinguir mayúsculas/acentos, que no
 *      es el orden de puntos de código que usa Python).
 *   3. `storeId` no filtra (Ruling 2).
 *   4. H1: el payload lleva el digest de CONTENIDO por partición, y ese
 *      digest se mueve cuando un valor cambia sin que cambie el conjunto de
 *      SKUs — que es el punto ciego permanente que H1 cierra.
 */
class ChecksumReaderTest extends TestCase
{
    use UnwrapsWebApiEnvelope;
    use BuildsAPermissiveStoreViewGuard;

    /**
     * Calculado independientemente en Python:
     *   hashlib.sha256("\n".join(sorted(["SKU-A","SKU-B","SKU-C"])).encode("utf-8")).hexdigest()
     * y en PHP:
     *   hash('sha256', implode("\n", (function () {
     *       $s = ["SKU-A","SKU-B","SKU-C"]; sort($s, SORT_STRING); return $s;
     *   })()))
     * Ambos producen el mismo valor.
     */
    private const EXPECTED_DIGEST = '2f4b9f61cc9b9f74f016af48a26e7e3e435fd778de2e359a8c5eef342e46aaff';

    public function testDigestMatchesThePythonAlgorithmForAKnownSkuSet(): void
    {
        $reader = $this->makeReader([
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
        $reader = $this->makeReader([
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

        $resultForStoreOne = $this->makeReader($entityRows)->getChecksums(storeId: 1);
        $resultForStoreTwo = $this->makeReader($entityRows)->getChecksums(storeId: 2);

        $this->assertSame($resultForStoreOne, $resultForStoreTwo);
    }

    /**
     * H1, LA prueba de la tarea: el mismo conjunto de SKUs con un valor
     * distinto. El conteo no se mueve, la huella del conjunto no se mueve
     * —eran las dos únicas señales que existían, y por eso un valor rancio
     * podía vivir para siempre—, y el digest de contenido de la partición
     * de ESE sku sí se mueve.
     */
    public function testAChangedValueMovesTheContentDigestAndNotTheSkuDigest(): void
    {
        $antes = $this->payloadOf($this->makeReader([
            $this->entityRow('SKU-A', '2026-09-01 08:30:00'),
            $this->entityRow('SKU-B', '2026-09-02 08:30:00'),
            $this->entityRow('SKU-C', '2026-09-03 08:30:00'),
        ])->getChecksums(storeId: 1));

        $despues = $this->payloadOf($this->makeReader([
            $this->entityRow('SKU-A', '2026-09-01 08:30:00'),
            $this->entityRow('SKU-B', '2026-12-25 23:59:59'),
            $this->entityRow('SKU-C', '2026-09-03 08:30:00'),
        ])->getChecksums(storeId: 1));

        $this->assertSame($antes['product_count'], $despues['product_count']);
        $this->assertSame($antes['sku_digest'], $despues['sku_digest']);
        $this->assertNotSame(
            $this->digestsByPartition($antes),
            $this->digestsByPartition($despues)
        );

        // Y sólo la partición de SKU-B cambia: es lo que convierte el remedio
        // en dirigido. Se nombra la partición esperada, no se cuenta cuántas
        // cambiaron, para que una implementación que moviera "alguna" no pase.
        $partitionOfB = (new ContentDigest())->partitionOf('SKU-B');
        $antesPorParticion = $this->digestsByPartition($antes);
        $despuesPorParticion = $this->digestsByPartition($despues);
        $this->assertNotSame($antesPorParticion[$partitionOfB], $despuesPorParticion[$partitionOfB]);
        unset($antesPorParticion[$partitionOfB], $despuesPorParticion[$partitionOfB]);
        $this->assertSame($antesPorParticion, $despuesPorParticion);
    }

    /**
     * El esquema de particionado viaja en el payload para que el lado Python
     * pueda exigir el acuerdo. Sin este número, dos lados que particionaran
     * distinto compararían conjuntos de claves disjuntos y el resultado
     * —"todo divergente" o, peor, "nada que comparar"— no significaría nada.
     */
    public function testThePartitionSchemeTravelsInThePayload(): void
    {
        $result = $this->payloadOf(
            $this->makeReader([$this->entityRow('SKU-A')])->getChecksums(storeId: 1)
        );

        $this->assertSame(ContentDigest::PARTITION_COUNT, $result['partition_count']);
        $this->assertSame(256, $result['partition_count']);
    }

    /**
     * El `updated_at` se lee en la MISMA consulta que el sku: dos consultas
     * darían el conjunto de un instante y los timestamps de otro, y esa
     * incoherencia se reportaría como deriva del espejo.
     */
    public function testTheSkuAndItsTimestampComeFromASingleQuery(): void
    {
        $queries = 0;
        $this->makeReader([$this->entityRow('SKU-A')], $queries)->getChecksums(storeId: 1);

        $this->assertSame(1, $queries);
    }

    /**
     * Un catálogo vacío no tiene particiones y no es un error: el conteo es 0
     * y el espejo, que también estará vacío, coincidirá.
     */
    public function testAnEmptyCatalogReportsNoPartitions(): void
    {
        $result = $this->payloadOf($this->makeReader([])->getChecksums(storeId: 1));

        $this->assertSame(0, $result['product_count']);
        $this->assertSame([], $result['content_partitions']);
    }

    /**
     * @param mixed[] $payload
     * @return array<string, string>
     */
    private function digestsByPartition(array $payload): array
    {
        return array_column($payload['content_partitions'], 'content_digest', 'partition');
    }

    /**
     * @return array<string, string>
     */
    private function entityRow(string $sku, string $updatedAt = '2026-09-01 08:30:00'): array
    {
        return ['sku' => $sku, 'updated_at' => $updatedAt];
    }

    /**
     * @param list<array<string, string>> $entityRows
     */
    private function makeReader(array $entityRows, ?int &$queries = null): ChecksumReader
    {
        $queries = 0;
        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('select')->willReturnCallback(
            static fn (): ChecksumFakeSelect => new ChecksumFakeSelect()
        );
        $connection->method('fetchAll')->willReturnCallback(
            function (ChecksumFakeSelect $select) use ($entityRows, &$queries): array {
                $queries++;
                return $entityRows;
            }
        );

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        return new ChecksumReader($resource, new ContentDigest(), $this->permissiveStoreViewGuard());
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
