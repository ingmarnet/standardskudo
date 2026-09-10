<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Standard\Skudo\Api\ChecksumReaderInterface;

/**
 * S0 Task 13: la red de seguridad de todo el sub-proyecto. Un delta puede
 * perderse (una cola truncada, un observer que no disparó en una importación
 * masiva por SQL directo, un reintento fallido) y, sin reconciliación, el
 * espejo se desvía en silencio: todos los scores de S1 heredan ese error sin
 * que nadie lo note. Este lector responde las dos preguntas que
 * `reconcile()` (lado Python, `src/skudo/ingest/reconcile.py`) necesita para
 * decidir si el espejo coincide con Magento: cuántos SKUs activos hay, y una
 * huella (digest) de cuáles son — para detectar el caso en que el conteo
 * coincide mientras el contenido no (un SKU borrado y otro creado en el
 * mismo intervalo).
 *
 * Ruling 3 (S0 Task 13, REVISADA sobre HTTP real): esta clase no agrega
 * NINGÚN filtro de versión propio. La versión anterior de esta ruling decía
 * lo contrario —delegaba en `ActiveVersionResolver`, que acotaba por reloj—
 * y era un error: bajo Magento_Staging `catalog_product_entity` sí tiene una
 * fila por VERSIÓN, pero Magento ya acota todo `Select` del framework a la
 * versión APLICADA por su cuenta, y anclar un segundo filtro en el reloj
 * produce un conjunto más estrecho que el que la tienda sirve. Como
 * `/products` sufría el MISMO doble filtro, los dos lados coincidían sobre
 * el conjunto estrechado y `reconcile()` reportaba "sin deriva" para
 * siempre: la red de seguridad quedaba ciega justo al hueco que existe para
 * detectar. Ver `Model\VersioningSchema`.
 *
 * Ruling 4 (S0 Task 13, cross-edition): esta clase no referencia ninguna
 * clase de `Magento\Staging\*` ni de Adobe Commerce, ni decide nada a partir
 * del edition string. Tras quitar el filtro propio no queda ni siquiera una
 * pregunta de esquema que hacer: el mismo código carga igual en Community y
 * en Adobe Commerce, y en cada una devuelve la población que ESA instancia
 * considera activa.
 */
class ChecksumReader implements ChecksumReaderInterface
{
    public function __construct(
        private readonly ResourceConnection $resource,
    ) {
    }

    /**
     * Ruling 2 (S0 Task 13): $storeId se acepta por simetría de interfaz y
     * uso futuro, pero NO filtra. Instrucción anterior (revocada): un
     * conteo/digest limitado a "visible en esa store view" parecía más
     * preciso, pero `full_sync` (lado Python) recorre `iter_products(store_id)`
     * SIN filtro de visibilidad — devuelve todo el catálogo activo para cada
     * store view — así que `mirror_count` es el mismo número para cualquier
     * store view. Si este método filtrara por visibilidad, jamás coincidiría
     * con el espejo y `reconcile()` exigiría un re-sync completo para
     * siempre, sin que ese re-sync pudiera arreglar nada (el remedio no
     * cambia el hecho de que ambos lados miden cosas distintas). Mirrorear el
     * catálogo completo por store view es deliberado: es lo que permite
     * reportar "este producto no aparece en la navegación de BR".
     */
    public function getChecksums(int $storeId): array
    {
        $connection = $this->resource->getConnection();
        $entity = $this->resource->getTableName('catalog_product_entity');

        // Sin where propio: la población es exactamente la que Magento
        // considera activa, la MISMA que devuelve `/products` (Ruling 3).
        $select = $connection->select()->from(['e' => $entity], ['sku' => 'e.sku']);

        $skus = $connection->fetchCol($select);

        // Ruling 1 (S0 Task 13): el orden se decide EN PHP, nunca con un
        // ORDER BY de SQL. Bajo la colación típica de MySQL/MariaDB para
        // esta columna (utf8mb4_general_ci), un ORDER BY ordena sin
        // distinguir mayúsculas/minúsculas ni acentos — produce una
        // secuencia distinta de la que da `sorted()` en Python, que ordena
        // por punto de código Unicode. Esa discrepancia haría que
        // sku_digest() (Python) y este método jamás coincidieran para
        // ningún catálogo con SKUs de distinta capitalización o con
        // acentos, y el remedio que reconcile() prescribe ante un digest
        // que no coincide — un re-sync completo — nunca lo repararía: los
        // dos lados seguirían ordenando distinto después del re-sync.
        // sort($skus, SORT_STRING) ordena por bytes de la cadena (el mismo
        // criterio, byte a byte, que sorted() de Python aplica sobre UTF-8),
        // así que ambos lados producen la MISMA secuencia sin necesidad de
        // coordinar colación ni paginación.
        sort($skus, SORT_STRING);

        return WebApiEnvelope::wrap([
            'product_count' => count($skus),
            'sku_digest' => hash('sha256', implode("\n", $skus)),
        ]);
    }
}
