<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ContentDigest;

/**
 * H1: el esquema de particionado y el digest de contenido tienen que
 * reproducirse IDÉNTICOS en PHP y en Python. Si no, `reconcile()` reporta
 * deriva permanente y el remedio que prescribe no la limpia nunca — el modo
 * de fallo del filtro de versión (C2) y del ORDER BY de SQL (Ruling 1).
 *
 * Los valores esperados de este archivo se calcularon en PYTHON, de forma
 * independiente, con `hashlib.sha256` y `sorted()`, y están fijados
 * literalmente en `tests/ingest/test_reconcile.py` del lado del ingestor. Si
 * alguien cambia el algoritmo en un solo lado, los dos archivos dejan de
 * coincidir y las dos suites fallan.
 */
class ContentDigestTest extends TestCase
{
    /**
     * Cálculo de referencia, en Python:
     *   hashlib.sha256("SKU-A".encode("utf-8")).hexdigest()[:2]  -> '0f'
     */
    public function testThePartitionOfASkuIsTheFirstByteOfItsSha256(): void
    {
        $digest = new ContentDigest();

        $this->assertSame('0f', $digest->partitionOf('SKU-A'));
        $this->assertSame('0b', $digest->partitionOf('SKU-C'));
        $this->assertSame('71', $digest->partitionOf('SKU-2'));
        $this->assertSame('71', $digest->partitionOf('SKU-764'));
        // UTF-8 sin normalizar: los bytes del SKU son la entrada, y el SKU
        // viaja intacto por todo el sistema (verificado sobre HTTP real).
        $this->assertSame('eb', $digest->partitionOf('SKU-ÑOÑO'));
    }

    /**
     * La partición NO depende de la capitalización porque no depende de
     * ninguna colación: son bytes. Dos SKUs que `utf8mb4_general_ci`
     * consideraría iguales caen en particiones distintas, que es lo correcto
     * (son productos distintos) y es justo lo que un `ORDER BY` de SQL no
     * podría reproducir.
     */
    public function testPartitioningIsByBytesAndNotByCollation(): void
    {
        $digest = new ContentDigest();

        $this->assertNotSame(
            $digest->partitionOf('SKU-ALPHA'),
            $digest->partitionOf('sku-alpha')
        );
    }

    /** @dataProvider timestampCases */
    public function testTheTimestampTokenIsCanonicalOrExplicitlyUnknown(
        ?string $raw,
        string $expected
    ): void {
        $this->assertSame($expected, (new ContentDigest())->timestampToken($raw));
    }

    /**
     * @return array<string, array{0: string|null, 1: string}>
     */
    public static function timestampCases(): array
    {
        return [
            'la forma normal de Magento' => ['2026-09-01 08:30:00', '2026-09-01 08:30:00'],
            // La trampa: si la columna tuviera precisión fraccionaria, el lado
            // Python no podría reproducir la fracción desde un timestamptz y
            // el digest discreparía PARA SIEMPRE. Se descarta al segundo, en
            // los dos lados, con la misma regla.
            'con fracción de segundo' => ['2026-09-01 08:30:00.123456', '2026-09-01 08:30:00'],
            'el cero-date que MySQL admite' => ['0000-00-00 00:00:00', 'desconocido'],
            'cadena vacía' => ['', 'desconocido'],
            'nulo' => [null, 'desconocido'],
            'basura' => ['no es una fecha', 'desconocido'],
            'con espacios alrededor' => ['  2026-09-01 08:30:00  ', '2026-09-01 08:30:00'],
        ];
    }

    /**
     * El digest de cada partición, contra los valores calculados en Python.
     * Las filas llegan en un orden que NO es el ordenado, para que un digest
     * que dependiera del orden de la consulta no coincidiera.
     */
    public function testPartitionDigestsMatchThePythonAlgorithm(): void
    {
        $partitions = (new ContentDigest())->partitions([
            ['sku' => 'SKU-ÑOÑO', 'updated_at' => '2026-09-06 08:30:00'],
            ['sku' => 'SKU-764', 'updated_at' => '2026-09-04 08:30:00'],
            ['sku' => 'SKU-A', 'updated_at' => '2026-09-01 08:30:00'],
            ['sku' => 'SKU-C', 'updated_at' => '0000-00-00 00:00:00'],
            ['sku' => 'SKU-2', 'updated_at' => '2026-09-02 08:30:00'],
        ]);

        $this->assertSame([
            ['partition' => '0b', 'product_count' => 1,
             'content_digest' => '9538a914923fb7f5e8ac7025f773606a379a86c6f7656275c2b79bbf6a17194f'],
            ['partition' => '0f', 'product_count' => 1,
             'content_digest' => '1432ee94047f9bf665ef47238a280a5e3a856654aa186aa00d1b4b83f8bd492b'],
            ['partition' => '71', 'product_count' => 2,
             'content_digest' => '0e8940b72b274ead34dd1a9a85fa5e9f2dcb1ca0250d534a8cd93c2e88536d81'],
            ['partition' => 'eb', 'product_count' => 1,
             'content_digest' => '273148f21b478033b76bff8f276b36f1ceb058765d3096c316634aa7ae0e241d'],
        ], $partitions);
    }

    /**
     * LA propiedad que H1 pide: un VALOR cambiado en un SKU existente mueve
     * el digest. Y mueve SÓLO el de su partición, que es lo que convierte el
     * remedio de "re-sincronizá 228.881 productos" en "revisá estos ~900".
     */
    public function testAChangedTimestampMovesOnlyItsOwnPartition(): void
    {
        $digest = new ContentDigest();
        $rows = [
            ['sku' => 'SKU-2', 'updated_at' => '2026-09-02 08:30:00'],
            ['sku' => 'SKU-764', 'updated_at' => '2026-09-04 08:30:00'],
            ['sku' => 'SKU-A', 'updated_at' => '2026-09-01 08:30:00'],
        ];
        $tocado = $rows;
        $tocado[1]['updated_at'] = '2026-09-09 09:09:09';

        $antes = $this->digestsByPartition($digest->partitions($rows));
        $despues = $this->digestsByPartition($digest->partitions($tocado));

        $this->assertNotSame($antes['71'], $despues['71']);
        $this->assertSame($antes['0f'], $despues['0f']);
        // Y el conteo de la partición no cambia: es un valor distinto, no un
        // producto de más. Un detector que sólo mirara conteos no lo vería.
        $this->assertSame(2, $this->countsByPartition($digest->partitions($tocado))['71']);
    }

    /**
     * Dos filas con el mismo SKU (una entidad versionada cuyas filas Magento
     * no acotó) no se colapsan: las dos entran en la línea de su partición.
     * Colapsarlas silenciosamente esconderría un desacuerdo real entre los
     * dos lados —el espejo tiene UNA fila por (sku, store view)— y el lugar
     * donde eso se decide no es acá.
     */
    public function testDuplicateSkusAreNotCollapsed(): void
    {
        $partitions = (new ContentDigest())->partitions([
            ['sku' => 'SKU-A', 'updated_at' => '2026-09-01 08:30:00'],
            ['sku' => 'SKU-A', 'updated_at' => '2026-12-01 08:30:00'],
        ]);

        $this->assertSame(1, count($partitions));
        $this->assertSame(2, $partitions[0]['product_count']);
    }

    public function testAnEmptyCatalogHasNoPartitions(): void
    {
        $this->assertSame([], (new ContentDigest())->partitions([]));
    }

    /**
     * `partitions()` devuelve una LISTA y no un mapa porque un array PHP con
     * claves '00'/'10'/'ff' es de claves mixtas (PHP convierte '10' en int) y
     * `json_encode` puede emitirlo como objeto o como array según los datos —
     * el bug que ya mordió tres veces en este módulo. La forma sobre el cable
     * la fija además `Test/Unit/WebApi/MapValuedFieldsAreJsonObjectsTest`.
     */
    public function testTheResultSerializesAsAJsonArrayOfObjects(): void
    {
        $partitions = (new ContentDigest())->partitions([
            ['sku' => 'SKU-A', 'updated_at' => '2026-09-01 08:30:00'],
        ]);

        $decoded = json_decode(json_encode($partitions), true);

        $this->assertIsList($decoded);
        $this->assertSame(['partition', 'product_count', 'content_digest'], array_keys($decoded[0]));
    }

    /**
     * @param list<array{partition: string, product_count: int, content_digest: string}> $partitions
     * @return array<string, string>
     */
    private function digestsByPartition(array $partitions): array
    {
        return array_column($partitions, 'content_digest', 'partition');
    }

    /**
     * @param list<array{partition: string, product_count: int, content_digest: string}> $partitions
     * @return array<string, int>
     */
    private function countsByPartition(array $partitions): array
    {
        return array_column($partitions, 'product_count', 'partition');
    }
}
