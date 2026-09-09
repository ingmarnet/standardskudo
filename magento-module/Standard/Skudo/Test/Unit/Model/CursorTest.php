<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

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

    public function testTamperedCursorIsRejected(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        (new Cursor())->decode('no-es-un-cursor');
    }

    public function testNegativeKeyIsRejected(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        (new Cursor())->encode(-1);
    }
}
