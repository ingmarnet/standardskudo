"""Detectores deterministas sobre el espejo. Sin IA, sin sonda, sin reglas curadas.

El aprendizaje que da forma a este módulo: las mismas ocho preguntas, hechas a
mano con SQL contra el catálogo de Renovapadel, dieron **tres respuestas
falsas**. 1.895 productos «sin imagen» que eran 197 porque el resto son
variantes que nadie navega; 375 «sin precio» que eran configurables, que por
definición lo heredan de sus hijos; y 3.681 «diferencias entre tiendas» que eran
un artefacto de la consulta.

Ninguna de las tres era un error de aritmética. Las tres fueron **evaluar a
productos a los que la pregunta no les aplicaba**. Por eso acá un detector no
devuelve una lista de hallazgos: devuelve hallazgos **y su cobertura**, y para
producirla tiene que decir explícitamente a quién evaluó, a quién no le aplica y
a quién no pudo mirar. El que no declara su alcance no compila.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# --- Vocabulario -----------------------------------------------------------

ALTA = "alta"
MEDIA = "media"
BAJA = "baja"
AVISO = "aviso"          # tiene efecto, pero la decisión es del dueño del catálogo
CANDIDATO = "candidato"  # exige revisión humana; nunca se presenta como veredicto

SEVERIDADES = (ALTA, MEDIA, BAJA, AVISO, CANDIDATO)

# Cuántos SKUs de un grupo se guardan. Antes eran 10 —suficiente para MOSTRAR
# ejemplos— y eso bastaba mientras el hallazgo sólo se leía. Ahora el score
# atribuye el hallazgo de grupo a cada uno de sus miembros, así que truncar la
# lista deja productos mal registrados con la nota intacta. El tope sigue
# existiendo para que un grupo patológico no haga una fila de megabytes, pero es
# alto y cuando actúa lo DICE: `truncado: true`.
MAX_SKUS_POR_GRUPO = 500

# Estados de un producto frente a la vitrina. `desconocido` no es un detalle:
# un producto cuyo `status` o `visibility` el espejo no informó no se evalúa, y
# eso se declara en la cobertura en vez de suponer que está publicado.
PUBLICADO = "publicado"
NO_NAVEGABLE = "no_navegable"
DESHABILITADO = "deshabilitado"
DESCONOCIDO = "desconocido"
OCULTO_SIN_STOCK = "oculto_sin_stock"

VISIBILIDADES_NAVEGABLES = {"2", "3", "4"}


@dataclass(frozen=True)
class Ficha:
    """Lo que un detector necesita de un producto en una store view."""

    sku: str
    attributes: dict
    attribute_set_id: int | None = None
    type_id: str | None = None
    categorias: tuple[int, ...] = ()
    # Stock por producto y config por store view. Ambos None por defecto para
    # que un llamador que no los provea obtenga el comportamiento de siempre.
    is_in_stock: bool | None = None
    muestra_sin_stock: bool | None = None

    def valor(self, code: str) -> str | None:
        """El valor de un campo, o `None` si está vacío.

        `"0"` es un valor presente: un peso declarado en cero es una afirmación,
        no una ausencia. Evaluar la verdad de la cadena en vez de su presencia
        es la trampa que convierte datos legítimos en carencias.
        """
        v = self.attributes.get(code)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @property
    def estado(self) -> str:
        status = self.valor("status")
        visibility = self.valor("visibility")
        if status is None or visibility is None:
            return DESCONOCIDO
        if status != "1":
            return DESHABILITADO
        if visibility not in VISIBILIDADES_NAVEGABLES:
            return NO_NAVEGABLE
        # Publicado por status+visibility. Solo lo oculta un sin-stock EXPLÍCITO
        # con una config de ocultar EXPLÍCITA: cualquier None deja PUBLICADO.
        if self.is_in_stock is False and self.muestra_sin_stock is False:
            return OCULTO_SIN_STOCK
        return PUBLICADO

    @property
    def prioridad_vitrina(self) -> str | None:
        """Prioridad de un producto ya publicado: 'pleno' o 'sin_stock'. None si
        no es publicado (los ocultos no se listan)."""
        if self.estado != PUBLICADO:
            return None
        if self.is_in_stock is False and self.muestra_sin_stock is True:
            return "sin_stock"
        return "pleno"


@dataclass(frozen=True)
class Hallazgo:
    code: str
    axis: int
    severity: str
    subject_type: str  # "producto" | "categoria" | "atributo" | "grupo"
    subject_key: str
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Cobertura:
    """Qué pudo mirar el detector. Es salida de primera clase, no un extra.

    `no_evaluado` nunca se cuenta como aprobado: un control que no se pudo
    correr queda pendiente, y el informe lo muestra. Un porcentaje de defectos
    sin su cobertura al lado es engañoso y el spec lo prohíbe.
    """

    detector: str
    evaluados: int = 0
    no_aplica: int = 0
    no_evaluado: int = 0
    motivo_no_aplica: str = ""

    @property
    def universo(self) -> int:
        return self.evaluados + self.no_aplica + self.no_evaluado


@dataclass(frozen=True)
class Resultado:
    hallazgos: list[Hallazgo]
    cobertura: Cobertura


# --- Utilidades ------------------------------------------------------------


def _particionar(fichas: Sequence[Ficha], aplica: Callable[[Ficha], bool]):
    """Separa en (evaluables, no_aplica, no_evaluado) según estado y predicado.

    Un solo lugar decide qué es "no pude mirar": el estado `desconocido`. Que
    esté acá y no en cada detector es lo que impide que uno se olvide y cuente
    su ignorancia como aprobación.
    """
    evaluables, no_aplica, no_evaluado = [], 0, 0
    for f in fichas:
        if f.estado == DESCONOCIDO:
            no_evaluado += 1
        elif aplica(f):
            evaluables.append(f)
        else:
            no_aplica += 1
    return evaluables, no_aplica, no_evaluado


def _publicado(f: Ficha) -> bool:
    return f.estado == PUBLICADO


def normalizar_nombre(nombre: str) -> str:
    """Para comparar nombres: sin tildes, sin mayúsculas, sin espacios dobles.

    No se usa para corregir nada, solo para agrupar candidatos. `CALZADO  ASICS`
    y `Calzado Asics` son el mismo nombre a los efectos de detectar repetición.
    """
    s = unicodedata.normalize("NFKD", nombre)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# --- Detectores ------------------------------------------------------------
#
# Cada uno declara a quién evalúa. El `motivo_no_aplica` no es documentación:
# es lo que el informe muestra para que nadie tenga que confiar en el número.


def _carencia(
    fichas: Sequence[Ficha], *, code: str, axis: int, severity: str, campo: str
) -> Resultado:
    """Un campo vacío en un producto publicado. La forma más común de hallazgo."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos = [
        Hallazgo(code, axis, severity, "producto", f.sku, {"campo": campo})
        for f in evaluables
        if f.valor(campo) is None
    ]
    return Resultado(
        hallazgos,
        Cobertura(
            code, len(evaluables), no_aplica, no_evaluado,
            "variantes no navegables y productos deshabilitados",
        ),
    )


def sin_imagen(fichas: Sequence[Ficha]) -> Resultado:
    """Publicado sin imagen principal.

    `no_selection` es el centinela de Magento para "sin imagen" y hay que
    tratarlo como vacío: es una cadena no vacía que significa ausencia, o sea
    exactamente el caso que un `if valor:` deja pasar.
    """
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos = [
        Hallazgo("sin_imagen", 7, ALTA, "producto", f.sku, {"campo": "image"})
        for f in evaluables
        if f.valor("image") in (None, "no_selection")
    ]
    return Resultado(
        hallazgos,
        Cobertura(
            "sin_imagen", len(evaluables), no_aplica, no_evaluado,
            "variantes no navegables y productos deshabilitados: una variante de "
            "talle no necesita foto propia",
        ),
    )


def sin_descripcion(fichas):
    return _carencia(fichas, code="sin_descripcion", axis=6, severity=MEDIA, campo="description")


def sin_descripcion_corta(fichas):
    return _carencia(
        fichas, code="sin_descripcion_corta", axis=6, severity=BAJA, campo="short_description"
    )


def sin_meta_title(fichas):
    return _carencia(fichas, code="sin_meta_title", axis=9, severity=MEDIA, campo="meta_title")


def sin_precio(fichas: Sequence[Ficha]) -> Resultado:
    """Publicado sin precio, **excluyendo los que no llevan precio propio**.

    Ésta es la exclusión que nació de un falso positivo real: 375 productos
    «sin precio» de los que 374 eran configurables. Un configurable, un
    agrupado o un bundle toma su precio de sus hijos; exigírselo es inventar un
    defecto en el 28 % del catálogo.
    """
    SIN_PRECIO_PROPIO = {"configurable", "grouped", "bundle"}
    evaluables, no_aplica, no_evaluado = _particionar(
        fichas, lambda f: _publicado(f) and (f.type_id or "simple") not in SIN_PRECIO_PROPIO
    )
    hallazgos = [
        Hallazgo("sin_precio", 8, ALTA, "producto", f.sku, {"campo": "price"})
        for f in evaluables
        if f.valor("price") is None or f.valor("price") == "0"
    ]
    return Resultado(
        hallazgos,
        Cobertura(
            "sin_precio", len(evaluables), no_aplica, no_evaluado,
            "configurables, agrupados y bundles, que toman el precio de sus hijos",
        ),
    )


def sin_categoria(fichas: Sequence[Ficha]) -> Resultado:
    """Publicado que no cuelga de ninguna categoría: existe y no se navega."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos = [
        Hallazgo("sin_categoria", 2, MEDIA, "producto", f.sku, {})
        for f in evaluables
        if not f.categorias
    ]
    return Resultado(
        hallazgos,
        Cobertura("sin_categoria", len(evaluables), no_aplica, no_evaluado,
                  "variantes no navegables y productos deshabilitados"),
    )


def nombre_en_mayusculas(fichas: Sequence[Ficha]) -> Resultado:
    """Nombre íntegramente en mayúsculas.

    Sale como `aviso` y no como defecto, a propósito: medido en el primer
    catálogo real, el 97 % de los nombres lo estaban. A ese nivel no es un
    descuido sino la convención de carga, y corregir en masa la convención de
    alguien sin preguntarle es exactamente lo que este producto no hace.

    El umbral de ocho caracteres evita marcar siglas y códigos, donde la
    mayúscula es correcta.
    """
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos = []
    for f in evaluables:
        nombre = f.valor("name")
        if nombre and len(nombre) > 8 and nombre == nombre.upper() and nombre != nombre.lower():
            hallazgos.append(
                Hallazgo("nombre_en_mayusculas", 1, AVISO, "producto", f.sku,
                         {"nombre": nombre[:80]})
            )
    return Resultado(
        hallazgos,
        Cobertura("nombre_en_mayusculas", len(evaluables), no_aplica, no_evaluado,
                  "variantes no navegables y productos deshabilitados"),
    )


def nombres_repetidos(fichas: Sequence[Ficha]) -> Resultado:
    """Varios productos publicados con el mismo nombre.

    **Un hallazgo por grupo, no por producto.** Ocho zapatillas con el mismo
    nombre son UNA causa —talles cargados como productos sueltos— y ocho
    hallazgos serían la misma causa contada ocho veces, que es la doble
    penalización que el spec prohíbe.

    Nunca se afirma que sean duplicados: dos registros con nombre idéntico
    pueden ser dos productos legítimamente distintos. Cuando el grupo es de
    tres o más productos **simples**, se marca como probable variante suelta,
    que es una hipótesis con más sustento y una corrección distinta.
    """
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    por_nombre: dict[str, list[Ficha]] = defaultdict(list)
    for f in evaluables:
        nombre = f.valor("name")
        if nombre:
            por_nombre[normalizar_nombre(nombre)].append(f)

    hallazgos = []
    for nombre, grupo in sorted(por_nombre.items()):
        if len(grupo) < 2:
            continue
        simples = [g for g in grupo if (g.type_id or "simple") == "simple"]
        variante_suelta = len(simples) >= 3 and len(simples) == len(grupo)
        hallazgos.append(
            Hallazgo(
                "variantes_sueltas" if variante_suelta else "nombre_repetido",
                1,
                ALTA if variante_suelta else CANDIDATO,
                "grupo",
                nombre[:120],
                {
                    "productos": len(grupo),
                    "skus": sorted(g.sku for g in grupo)[:MAX_SKUS_POR_GRUPO],
                    "truncado": len(grupo) > MAX_SKUS_POR_GRUPO,
                    "lectura": (
                        "probables talles del mismo modelo cargados como productos sueltos"
                        if variante_suelta
                        else "mismo nombre; puede ser duplicado o dos productos distintos"
                    ),
                },
            )
        )
    return Resultado(
        hallazgos,
        Cobertura("nombres_repetidos", len(evaluables), no_aplica, no_evaluado,
                  "variantes no navegables y productos deshabilitados"),
    )


# Tokens que denotan un TALLE dentro del nombre. La lista es corta a propósito:
# quitar cualquier número agruparía «AT10 2024» con «AT10 2025», que son dos
# productos distintos. Sólo se quitan las formas que no pueden ser otra cosa —
# decimales con coma o apóstrofo, y marcadores explícitos de talle.
# `\b` no sirve de cierre acá: después de un apóstrofo no hay frontera de
# palabra —los dos son caracteres no-palabra— así que `5,5'` quedaba a medio
# quitar y `6'` no se quitaba nunca. Se cierra con `(?!\w)` y se abre con un
# lookbehind que impide casar dentro de una palabra (la `t` de `at10`).
_TALLE = re.compile(
    r"(?<![\w.,])("
    r"\d{1,2}[.,]\d\s*'?"        # 5,5'   6.5
    r"|\d{1,2}\s*'"              # 6'
    r"|talles?\s*\d{1,3}"
    r"|t\s?\d{2}"                # T40
    r"|xxs|xs|xl|xxl|xxxl"
    r")(?!\w)"
)


def sin_talle(nombre: str) -> tuple[str, str]:
    """El nombre sin su talle, y el talle que se quitó.

    Devuelve las dos mitades porque la segunda es la que valida el hallazgo: si
    todos los miembros de un grupo tenían el MISMO talle, entonces no fue el
    talle lo que los separaba y no son una familia.
    """
    base = normalizar_nombre(nombre)
    quitados = " ".join(m.group(0).strip() for m in _TALLE.finditer(base))
    return re.sub(r"\s+", " ", _TALLE.sub(" ", base)).strip(), quitados


def variantes_por_talle(fichas: Sequence[Ficha]) -> Resultado:
    """Familias de talles publicadas como productos sueltos.

    El caso que lo motivó: ocho zapatillas `simple`, visibles en catálogo y
    búsqueda, cuyos nombres sólo se diferencian en `5,5'`, `6'`, `6,5'`… Son
    ocho filas casi idénticas en los resultados de búsqueda de un comprador que
    busca un modelo, cuando deberían ser un producto con selector de talle.

    Se descubrió porque un detector anterior —comparación de nombres exactos—
    devolvió cero: los nombres NO son iguales, difieren justamente en el talle.
    Un detector honesto que no encuentra nada no es lo mismo que la ausencia del
    problema.

    Tres condiciones, todas necesarias: al menos tres productos, todos simples
    y publicados, y **al menos tres talles distintos** entre ellos. La última es
    la que impide que un grupo unido por casualidad pase por familia.
    """
    evaluables, no_aplica, no_evaluado = _particionar(
        fichas, lambda f: _publicado(f) and (f.type_id or "simple") == "simple"
    )
    familias: dict[str, list[tuple[Ficha, str]]] = defaultdict(list)
    for f in evaluables:
        nombre = f.valor("name")
        if not nombre:
            continue
        base, talle = sin_talle(nombre)
        if talle and len(base) > 12:
            familias[base].append((f, talle))

    hallazgos = []
    for base, grupo in sorted(familias.items()):
        talles = {t for _, t in grupo}
        if len(grupo) < 3 or len(talles) < 3:
            continue
        hallazgos.append(
            Hallazgo(
                "variantes_por_talle", 1, ALTA, "grupo", base[:120],
                {
                    "productos": len(grupo),
                    "talles": sorted(talles)[:12],
                    "skus": sorted(f.sku for f, _ in grupo)[:MAX_SKUS_POR_GRUPO],
                    "truncado": len(grupo) > MAX_SKUS_POR_GRUPO,
                    "lectura": "talles del mismo modelo publicados como productos "
                               "sueltos; deberían ser variantes de un configurable",
                },
            )
        )
    return Resultado(
        hallazgos,
        Cobertura("variantes_por_talle", len(evaluables), no_aplica, no_evaluado,
                  "configurables, variantes no navegables y deshabilitados"),
    )


DETECTORES: tuple[Callable[[Sequence[Ficha]], Resultado], ...] = (
    sin_imagen,
    sin_precio,
    nombres_repetidos,
    variantes_por_talle,
    sin_descripcion,
    sin_descripcion_corta,
    sin_meta_title,
    sin_categoria,
    nombre_en_mayusculas,
)


def evaluar(fichas: Sequence[Ficha]) -> list[Resultado]:
    """Corre todos los detectores en orden estable."""
    return [d(fichas) for d in DETECTORES]
