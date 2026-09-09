<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

/**
 * Cursor de paginación por keyset.
 *
 * Se pagina por la clave de entidad y NO por offset: en un catálogo de cientos
 * de miles de productos el offset degrada de forma cuadrática y además puede
 * saltarse o repetir filas cuando el catálogo cambia entre páginas.
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
            throw new \InvalidArgumentException('cursor inválido');
        }

        $value = substr($decoded, strlen(self::PREFIX));
        if (!ctype_digit($value)) {
            throw new \InvalidArgumentException('cursor inválido');
        }

        return (int) $value;
    }
}
