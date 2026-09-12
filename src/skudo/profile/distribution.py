"""Qué valores toma un atributo, sin adivinar ninguno.

El parseo numérico es el punto donde este módulo puede hacer daño: leer
`1,250` como 1,25 cuando eran 1250 gramos produce un dato falso con aspecto de
medición, y la distribución entera hereda el error. Cuando las dos lecturas son
plausibles, la respuesta es "ambiguo" y el valor se cuenta aparte.
"""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

# Cuántos valores distintos se guardan de cada atributo. Suficiente para que un
# humano reconozca el patrón en la curación, y acotado para que el perfil no se
# convierta en una copia del catálogo.
TOP_VALORES = 10

# Proporción de valores presentes que tienen que leerse como número para tratar
# el atributo como numérico. Magento declara casi todo como `text`, así que el
# tipo hay que medirlo; y un puñado de números sueltos dentro de un campo de
# texto no lo convierte en una magnitud.
MIN_SHARE_NUMERICO = 0.8

_SOLO_NUMERO = re.compile(r"^[+-]?\d+([.,]\d+)*$")


@dataclass(frozen=True)
class Lectura:
    valor: float | None
    motivo: str  # "leido" | "ambiguo" | "no_numerico"


def parse_number(raw: object) -> Lectura:
    texto = str(raw).strip()
    if not _SOLO_NUMERO.match(texto):
        return Lectura(None, "no_numerico")
    separadores = [c for c in texto if c in ".,"]
    if not separadores:
        return Lectura(float(texto), "leido")
    if len(set(separadores)) == 2:
        # Hay punto y coma: el ÚLTIMO es el decimal, en las dos convenciones.
        ultimo = max(texto.rfind("."), texto.rfind(","))
        entero = re.sub(r"[.,]", "", texto[:ultimo])
        return Lectura(float(f"{entero}.{texto[ultimo + 1:]}"), "leido")
    if len(separadores) > 1:
        # Un mismo separador repetido sólo puede ser de miles: 1.234.567.
        return Lectura(float(re.sub(r"[.,]", "", texto)), "leido")
    posicion = max(texto.rfind("."), texto.rfind(","))
    decimales = len(texto) - posicion - 1
    if decimales == 3:
        return Lectura(None, "ambiguo")
    return Lectura(float(texto[:posicion] + "." + texto[posicion + 1 :]), "leido")


def percentil(valores_ordenados: Sequence[float], k: int) -> float:
    """Rango más cercano, sin interpolar.

    Interpolar inventa un valor que nadie tiene, y estas distribuciones se usan
    para decidir si un peso es implausible: el umbral tiene que ser un valor
    que exista en el catálogo.
    """
    n = len(valores_ordenados)
    indice = max(1, min(n, -(-k * n // 100)))
    return valores_ordenados[indice - 1]


@dataclass(frozen=True)
class Stats:
    kind: str
    n_present: int
    n_ambiguous: int = 0
    minimum: float | None = None
    p05: float | None = None
    p50: float | None = None
    p95: float | None = None
    maximum: float | None = None
    distinct_values: int | None = None
    mode_share: float | None = None
    discriminating_power: float | None = None
    top_values: list = field(default_factory=list)


def value_stats(valores: Sequence[object], frontend_input: str) -> Stats:
    """La distribución de UN atributo dentro de UNA partición."""
    presentes = [v for v in valores]
    if not presentes:
        return Stats(kind="texto", n_present=0)

    lecturas = [parse_number(v) for v in presentes]
    numeros = sorted(l.valor for l in lecturas if l.motivo == "leido")
    ambiguos = sum(1 for l in lecturas if l.motivo == "ambiguo")

    conteo = Counter(str(v) for v in presentes)
    # Orden estable: primero por frecuencia descendente, luego alfabético. Sin
    # el desempate alfabético, dos pasadas iguales podrían listar distinto.
    top = sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_VALORES]
    moda = max(conteo.values()) / len(presentes)

    es_numerico = bool(numeros) and len(numeros) >= MIN_SHARE_NUMERICO * len(presentes)
    if es_numerico:
        kind = "numerico"
    elif frontend_input in {"select", "multiselect", "boolean"}:
        kind = "opcion"
    else:
        kind = "texto"

    return Stats(
        kind=kind,
        n_present=len(presentes),
        n_ambiguous=ambiguos,
        minimum=numeros[0] if numeros else None,
        p05=percentil(numeros, 5) if numeros else None,
        p50=percentil(numeros, 50) if numeros else None,
        p95=percentil(numeros, 95) if numeros else None,
        maximum=numeros[-1] if numeros else None,
        distinct_values=len(conteo),
        mode_share=moda,
        discriminating_power=1.0 - moda,
        top_values=[[valor, n] for valor, n in top],
    )
