<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\Exception\InputException;
use Magento\Framework\Exception\LocalizedException;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\Cursor;

class CursorTest extends TestCase
{
    public function testRoundTrip(): void
    {
        $cursor = new Cursor();
        $this->assertSame(4821, $cursor->decode($cursor->encode(4821)));
    }

    public function testEmptyCursorMeansStartFromTheBeginning(): void
    {
        $this->assertSame(0, (new Cursor())->decode(null));
        $this->assertSame(0, (new Cursor())->decode(''));
    }

    /**
     * M2: un cursor ilegible es entrada inválida del CLIENTE, así que la
     * excepción tiene que ser una que el framework de web API mapee a 400.
     * `\InvalidArgumentException` no lo es, y sobre HTTP real
     * `/products?cursor=BASURA` devolvía 500 con la traza completa y rutas
     * absolutas del sistema de archivos.
     *
     * Se afirma `InputException` Y que sea `LocalizedException`: lo primero
     * fija la clase, lo segundo fija la PROPIEDAD de la que depende el 400
     * (el `ErrorProcessor` de Magento mira si es LocalizedException, no si es
     * InputException). Si alguien la cambiara por otra excepción de Magento
     * que no sea localizada, la segunda aserción lo señala.
     */
    public function testATamperedCursorIsRejectedAsClientInputNotAsAServerFault(): void
    {
        try {
            (new Cursor())->decode('no-es-un-cursor');
            $this->fail('un cursor ilegible tiene que lanzar');
        } catch (\Throwable $e) {
            $this->assertInstanceOf(InputException::class, $e);
            $this->assertInstanceOf(LocalizedException::class, $e);
            $this->assertStringNotContainsString(
                'no-es-un-cursor',
                $e->getMessage(),
                'el mensaje de error no debe repetir texto que el cliente controla'
            );
        }
    }

    /**
     * Un base64 válido con el prefijo correcto pero una clave no numérica
     * también es entrada del cliente: mismo 400, no un 500.
     */
    public function testACursorWithANonNumericKeyIsAlsoClientInput(): void
    {
        $this->expectException(InputException::class);
        (new Cursor())->decode(base64_encode('skudo1:abc'));
    }

    /**
     * `encode()` sigue lanzando `\InvalidArgumentException` a propósito: una
     * clave negativa no viene de la red, viene de un error de programación
     * del propio módulo, y ahí un 500 es la respuesta correcta.
     */
    public function testANegativeKeyIsAProgrammingErrorNotClientInput(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        (new Cursor())->encode(-1);
    }
}
