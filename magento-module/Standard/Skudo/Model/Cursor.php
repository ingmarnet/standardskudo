<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\Exception\InputException;

/**
 * Cursor de paginación por keyset.
 *
 * Se pagina por la clave de entidad y NO por offset: en un catálogo de cientos
 * de miles de productos el offset degrada de forma cuadrática y además puede
 * saltarse o repetir filas cuando el catálogo cambia entre páginas.
 *
 * M2 — por qué `decode()` lanza `InputException` y no
 * `\InvalidArgumentException`: el cursor lo provee el CLIENTE, así que un
 * cursor ilegible es una entrada inválida (400) y no un fallo del servidor.
 * `\InvalidArgumentException` no es una excepción de Magento, y el framework
 * de web API renderiza cualquier cosa que no reconozca como error 500 CON LA
 * TRAZA COMPLETA: verificado sobre HTTP real, `/products?cursor=BASURA`
 * devolvía `500 {"message":"cursor inválido","trace":"#0 .../Model/
 * ProductReader.php(34): ..."}`. Dos problemas en uno: el cliente Python hace
 * `raise_for_status()` y un 500 es la clase de error que se reintenta —un 400
 * no—, y el cuerpo filtraba rutas absolutas del sistema de archivos en un
 * endpoint público (en modo `production` Magento omite la traza, pero el
 * código de estado sigue siendo el equivocado). `InputException` es
 * `LocalizedException`, que el `ErrorProcessor` de web API mapea a 400 sin
 * traza — el MISMO camino que ya usaba el tope de 100 SKUs de
 * `ProductReader::getBySku()`, que sí devolvía 400.
 *
 * `encode()` sigue lanzando `\InvalidArgumentException`: una clave negativa
 * no viene de la red, viene de un error de programación del propio módulo.
 * Ahí un 500 es la respuesta correcta.
 */
class Cursor
{
    private const PREFIX = 'skudo1:';

    public function encode(int $lastKey): string
    {
        if ($lastKey < 0) {
            throw new \InvalidArgumentException('la clave del cursor no puede ser negativa');
        }
        return base64_encode(self::PREFIX . $lastKey);
    }

    public function decode(?string $cursor): int
    {
        if ($cursor === null || $cursor === '') {
            return 0;
        }

        $decoded = base64_decode($cursor, true);
        if ($decoded === false || !str_starts_with($decoded, self::PREFIX)) {
            throw self::invalid();
        }

        $value = substr($decoded, strlen(self::PREFIX));
        if (!ctype_digit($value)) {
            throw self::invalid();
        }

        return (int) $value;
    }

    /**
     * El mensaje NO repite el cursor recibido: es texto que el cliente
     * controla y viajaría de vuelta en un cuerpo de error.
     */
    private static function invalid(): InputException
    {
        return new InputException(__(
            'cursor inválido: se esperaba un cursor emitido por este módulo '
            . '(base64 de "%1<clave>")',
            self::PREFIX
        ));
    }
}
