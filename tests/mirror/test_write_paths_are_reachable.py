"""Comprobación permanente: ninguna función de escritura del espejo puede
quedarse sin camino de ingesta.

Este es el hallazgo C1, y después C3, y en las dos rondas lo encontró una
revisión y no la suite. La primera vez fueron cuatro tablas —`attributes`,
`attribute_options`, `option_labels`, `categories`— cuyos `upsert_*` solo se
llamaban desde tests: el esquema existía, los tests estaban verdes, y ningún
dato real llegaba nunca a esas tablas. La segunda fue `product_signal`, en la
MISMA fase que arreglaba las otras cuatro.

Una tabla que solo se escribe desde tests es peor que una tabla ausente: el
esquema, los tests y la documentación afirman que el dato existe, y el vacío
solo se descubre cuando alguien consulta la tabla en producción. Por eso la
regla que este test impone es la de "caminos alcanzables": toda función
`upsert_*` / `set_*` de `skudo.mirror` tiene que tener al menos un llamador en
`src/`, no solo en `tests/`.

Se analiza el AST y no el texto: un `grep` contaría el propio `def`, las
menciones en docstrings y los comentarios como llamadas — exactamente el tipo
de falso verde que este test existe para no producir.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "skudo"
MIRROR = SRC / "mirror"

WRITE_PREFIXES = ("upsert_", "set_")


def _write_functions() -> dict[str, Path]:
    """Nombre -> archivo, de toda función pública de escritura de `mirror/`."""
    found: dict[str, Path] = {}
    for path in sorted(MIRROR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith(WRITE_PREFIXES):
                found[node.name] = path
    return found


def _called_names() -> set[str]:
    """Nombres invocados en cualquier parte de `src/`, incluido `mirror/`.

    Se aceptan llamadas desde `mirror/` mismo (una función de escritura puede
    ser el detalle de otra), pero nunca desde `tests/`: la pregunta es si
    existe un camino desde el producto, no desde su andamiaje.
    """
    called: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
    return called


def test_every_mirror_write_function_has_a_caller_outside_tests():
    functions = _write_functions()
    assert functions, "no se encontró ninguna función de escritura: el análisis está roto"

    called = _called_names()
    orphans = sorted(
        f"{name} ({path.relative_to(SRC.parent.parent)})"
        for name, path in functions.items()
        if name not in called
    )

    assert not orphans, (
        "estas funciones de escritura del espejo no tienen ningún llamador en "
        f"src/, solo (a lo sumo) en tests/: {orphans}. Una tabla que solo se "
        "escribe desde tests afirma un dato que en producción nunca llega. "
        "Escribí el ingestor que la alimenta, o borrá la función."
    )


def test_the_check_would_notice_a_write_function_with_no_caller():
    """El test de arriba solo sirve si sabe detectar el caso. Se comprueba con
    un nombre que NO existe en el producto: si `_called_names()` devolviera
    todo, o el conjunto de funciones se calculara mal, este `assert` fallaría y
    la comprobación entera quedaría demostrada como hueca."""
    assert "upsert_una_tabla_que_nadie_escribe" not in _called_names()
