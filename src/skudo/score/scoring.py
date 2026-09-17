"""La nota de un producto y la salud de un catálogo.

La forma viene de las herramientas de referencia, y cada préstamo está anotado
donde se usa: el grado A–E por producto y por store view es de Akeneo, la salud
0–100 independiente del tamaño del catálogo es de Semrush, y los errores
críticos que pesan aparte del número son de Google Merchant Center.

Lo que ninguna de las tres hace, y acá es obligatorio: **el número se explica**.
La documentación de Semrush dice con todas las letras que su fórmula no se
publica. Un catálogo que saca C tiene derecho a preguntar por qué, así que cada
nota viaja con las deducciones que la produjeron y con la cobertura de los
controles que se pudieron correr.
"""

from dataclasses import dataclass, field

from skudo.findings.catalog import ALTA, AVISO, BAJA, CANDIDATO, MEDIA

# --- Pesos -----------------------------------------------------------------
#
# Constantes con su razón al lado, no configuración: un peso que se puede
# ajustar por tenant termina ajustado hasta que el catálogo apruebe, y entonces
# la nota deja de significar nada entre clientes. Es la misma lección de los
# umbrales del perfilador.

PESO_POR_SEVERIDAD = {
    # Un defecto que impide o frena la venta.
    ALTA: 25,
    # Una carencia real que no bloquea la transacción.
    MEDIA: 10,
    # Una carencia menor.
    BAJA: 5,
    # Exige revisión humana y puede no ser un defecto: pesa poco a propósito,
    # porque penalizar fuerte una sospecha castiga al que tiene un catálogo
    # difícil, no al que lo tiene mal.
    CANDIDATO: 3,
    # Decisión del dueño del catálogo (2026-09-16). Una convención editorial
    # —los nombres en mayúsculas, el 97 % de Renovapadel— no es un defecto. Se
    # reporta con su recuento y fuera del número; si penalizara, el catálogo
    # entero quedaría condenado por un estilo que alguien eligió.
    AVISO: 0,
}

# Los dos que impiden la transacción (decisión del usuario, 2026-09-16). Pesan
# APARTE del score, como los "product issues" de Merchant Center: un producto
# puede tener buena nota y aun así no estar listo para publicar, y presentar las
# dos cosas como un solo número esconde justo eso.
CODIGOS_CRITICOS = frozenset({"sin_precio", "sin_imagen"})

# Los cortes del grado. Se eligieron contra casos concretos y no en abstracto:
# un producto al que sólo le falta la descripción corta (−5) sigue siendo A; si
# le falta la descripción (−10) baja a B; sin foto (−25) es C.
CORTES = ((95, "A"), (80, "B"), (60, "C"), (40, "D"), (0, "E"))


def grado(puntaje: float) -> str:
    for minimo, letra in CORTES:
        if puntaje >= minimo:
            return letra
    return "E"


@dataclass(frozen=True)
class HallazgoDeProducto:
    """Un hallazgo ya atribuido a un producto concreto."""

    code: str
    severity: str
    axis: int
    # La causa raíz. Cuando dos hallazgos la comparten —un detector y una regla
    # sobre el mismo atributo— son UNA causa y pesan una vez (spec §6.4: sin
    # doble penalización). None → la causa es el propio code.
    causa: str | None = None


@dataclass(frozen=True)
class NotaDeProducto:
    sku: str
    puntaje: int
    grado: str
    critico: bool
    # Qué le descontó cada hallazgo. Es lo que permite contestar «¿por qué soy
    # una C?» sin que nadie tenga que leer el código.
    deducciones: list = field(default_factory=list)


def nota_de_producto(sku: str, hallazgos: list[HallazgoDeProducto]) -> NotaDeProducto:
    """Cien menos lo que cada hallazgo descuenta, con piso en cero.

    Un mismo código no descuenta dos veces aunque llegue repetido —puede venir
    del detector por producto y del de grupo—: se penaliza la causa, no sus
    manifestaciones, que es lo que el spec llama deduplicación por causa raíz.

    La causa raíz es `h.causa`, o el propio `code` cuando no hay causa (los
    hallazgos sin evidencia de atributo, que dedupan como siempre). Cuando dos
    hallazgos comparten causa —un detector y una regla sobre el mismo
    atributo, spec §6.4— se conserva el de mayor peso de severidad, porque el
    detector especial suele ser más específico; ante empate, el primero que
    llegó.
    """
    vistos: dict[str, HallazgoDeProducto] = {}
    for h in hallazgos:
        clave = h.causa or h.code
        actual = vistos.get(clave)
        if actual is None or PESO_POR_SEVERIDAD.get(h.severity, 0) > PESO_POR_SEVERIDAD.get(
            actual.severity, 0
        ):
            vistos[clave] = h

    deducciones = []
    total = 0
    for clave in sorted(vistos):
        h = vistos[clave]
        peso = PESO_POR_SEVERIDAD.get(h.severity, 0)
        if peso:
            deducciones.append({"code": h.code, "severidad": h.severity, "resta": peso})
            total += peso

    puntaje = max(0, 100 - total)
    return NotaDeProducto(
        sku=sku,
        puntaje=puntaje,
        grado=grado(puntaje),
        critico=any(h.code in CODIGOS_CRITICOS for h in vistos.values()),
        deducciones=deducciones,
    )


@dataclass(frozen=True)
class SaludDeCatalogo:
    salud: int
    productos: int
    distribucion: dict
    criticos: int
    por_eje: dict

    @property
    def grado(self) -> str:
        return grado(self.salud)


def salud_de_catalogo(
    notas: list[NotaDeProducto],
    hallazgos_por_producto: dict[str, list[HallazgoDeProducto]] | None = None,
) -> SaludDeCatalogo:
    """La salud del catálogo: el promedio de las notas de sus productos.

    Promedio y no suma de defectos, y esa elección es de Semrush: su
    documentación dice que el Site Health **no depende del número de páginas**
    porque cuenta la frecuencia de cada fallo. Un promedio tiene esa propiedad
    por construcción — un catálogo de 3.681 productos y uno de 228.881 se
    comparan sin corregir nada.

    Y va acompañado de la DISTRIBUCIÓN de grados, que es de Akeneo, porque un
    promedio de 93 puede esconder un 10 % de productos en E. Las dos cifras
    dicen cosas distintas y ninguna reemplaza a la otra.
    """
    if not notas:
        return SaludDeCatalogo(0, 0, {letra: 0 for _, letra in CORTES}, 0, {})

    distribucion = {letra: 0 for _, letra in CORTES}
    for n in notas:
        distribucion[n.grado] += 1

    por_eje: dict[int, int] = {}
    for hallazgos in (hallazgos_por_producto or {}).values():
        for h in hallazgos:
            peso = PESO_POR_SEVERIDAD.get(h.severity, 0)
            if peso:
                por_eje[h.axis] = por_eje.get(h.axis, 0) + peso

    return SaludDeCatalogo(
        salud=round(sum(n.puntaje for n in notas) / len(notas)),
        productos=len(notas),
        distribucion=distribucion,
        criticos=sum(1 for n in notas if n.critico),
        por_eje=dict(sorted(por_eje.items())),
    )


def impacto(severidad: str, productos_afectados: int) -> int:
    """Para ordenar los hallazgos en el informe.

    De Merchant Center, que ordena por impacto en visibilidad y no por cantidad.
    Sin esto, 1.308 avisos de mayúsculas —que además no penalizan— aparecerían
    arriba de 197 productos sin foto.
    """
    return PESO_POR_SEVERIDAD.get(severidad, 0) * productos_afectados
