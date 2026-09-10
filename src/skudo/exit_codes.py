"""Códigos de salida de los comandos del ingestor.

Viven en su propio módulo porque los usan el CLI (`skudo.cli`) y el arnés de
aceptación (`skudo.acceptance.s0`), y dos definiciones de la misma convención
—una por comando— es cómo un `2` acaba significando "tenant desconocido" en un
lado y "argumento inválido" en el otro para el que automatiza.

    0   la operación hizo lo que dice
    1   fallo de la operación: error del módulo, deriva detectada, criterio de
        aceptación reprobado, estado insuficiente
    2   tenant desconocido
    3   configuración incompleta: falta `SKUDO_DATABASE_URL`, o la variable de
        entorno donde la fila del tenant declara su token
    64  error de USO: argumento inválido o comando desconocido

El 64 existe porque `argparse` sale con 2 ante un argumento mal escrito, y 2
ya significa "tenant desconocido": sin separarlos, un `--stores` mal tecleado
y un tenant inexistente serían el mismo código. 64 es `EX_USAGE` de
`sysexits.h`.
"""

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_UNKNOWN_TENANT = 2
EXIT_CONFIGURATION = 3
EXIT_USAGE = 64
