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
from difflib import SequenceMatcher

from skudo.profile.distribution import parse_number, value_stats

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
        """Prioridad de un producto ya publicado: 'pleno', 'sin_stock' o
        'desconocido'. None si no es publicado (los ocultos no se listan).

        `is_in_stock is None` es stock no sincronizado, no stock pleno: la
        realidad de hoy (sin datos de stock cargados) no puede pintarse como
        "con stock" para todo el catálogo. Confundir un desconocido con un
        veredicto es exactamente lo que el spec §6 prohíbe.
        """
        if self.estado != PUBLICADO:
            return None
        if self.is_in_stock is True:
            return "pleno"
        if self.is_in_stock is False:
            return "sin_stock"
        return "desconocido"


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


# El umbral de similitud para llamar «casi idénticos» a dos nombres. Es una
# hipótesis hasta calibrarlo contra el catálogo real (spec §6.1: «la similitud
# genera un candidato, nunca un veredicto»). 0.90 deja pasar «silla gamer roja»/
# «silla gamer rojo» y rechaza «remera roja»/«remera azul» (ratio ~0.75).
# ponytail: umbral fijo; calibrar con la muestra etiquetada del arnés de FP.
UMBRAL_SIMILITUD = 0.90
# Un nombre tan corto no identifica un producto, lo identifica la categoría:
# «mesa» vs «mesas» no es un duplicado, es vocabulario. Mismo piso que el
# `len(base) > 12` de las familias de talles.
MIN_BASE = 12


def duplicado(fichas: Sequence[Ficha]) -> Resultado:
    """Candidatos a duplicado por nombre CASI idéntico, no por nombre igual.

    Los nombres idénticos ya los marca `nombres_repetidos` y las familias de
    talles `variantes_por_talle`. Éste mira lo que a ambos se les escapa: dos
    registros cuyo nombre difiere en poco —una errata, un plural, «rojo» por
    «roja»— que bien pueden ser el mismo producto. Sale `candidato` a propósito:
    confirmar equivalencia exige revisar capacidad, color, revisión, región y
    presentación.

    Un producto cuyo nombre ya se repite exacto no se reconsidera acá: su
    hallazgo (más fuerte, `variantes_sueltas`/`nombre_repetido`) ya existe, y
    volver a marcarlo sería la misma causa contada dos veces.
    """
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    por_nombre: dict[str, list[Ficha]] = defaultdict(list)
    for f in evaluables:
        nombre = f.valor("name")
        if nombre:
            por_nombre[normalizar_nombre(nombre)].append(f)

    unicos = sorted(n for n, grupo in por_nombre.items() if len(grupo) == 1)

    vecinos: dict[str, set[str]] = defaultdict(set)
    # ponytail: O(n²) con quick_ratio de atajo; bloquear por primer token si N
    # crece de ~10⁴ (hoy Renovapadel ~10³ nombres únicos).
    for i, a in enumerate(unicos):
        if len(a) < MIN_BASE:
            continue
        matcher = SequenceMatcher(None, a)
        for b in unicos[i + 1 :]:
            if len(b) < MIN_BASE:
                continue
            # Una diferencia que es sólo de talle no es un duplicado: es la
            # familia de talles que `variantes_por_talle` ya mira.
            if sin_talle(a)[0] == sin_talle(b)[0]:
                continue
            matcher.set_seq2(b)
            if (
                matcher.real_quick_ratio() >= UMBRAL_SIMILITUD
                and matcher.quick_ratio() >= UMBRAL_SIMILITUD
                and matcher.ratio() >= UMBRAL_SIMILITUD
            ):
                vecinos[a].add(b)
                vecinos[b].add(a)

    # Componentes conexas del grafo de similitud: un grupo, un hallazgo.
    visitados: set[str] = set()
    hallazgos: list[Hallazgo] = []
    for nombre in unicos:
        if nombre in visitados:
            continue
        componente: list[str] = []
        pila = [nombre]
        while pila:
            n = pila.pop()
            if n in visitados:
                continue
            visitados.add(n)
            componente.append(n)
            pila.extend(vecinos[n] - visitados)
        if len(componente) < 2:
            continue
        skus = sorted(f.sku for n in componente for f in por_nombre[n])
        hallazgos.append(
            Hallazgo(
                "duplicado",
                1,
                CANDIDATO,
                "grupo",
                min(componente, key=len)[:120],
                {
                    "productos": len(skus),
                    "skus": skus[:MAX_SKUS_POR_GRUPO],
                    "truncado": len(skus) > MAX_SKUS_POR_GRUPO,
                    "nombres": sorted(componente)[:12],
                    "lectura": "nombres casi idénticos; pueden ser el mismo producto "
                               "o dos productos distintos",
                },
            )
        )
    return Resultado(
        hallazgos,
        Cobertura(
            "duplicado",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y productos deshabilitados",
        ),
    )


# Eje 4: sospecha de conversión de unidad. Un factor ×10/×100/×1000 limpio
# contra la distribución del tipo señala gramos por kilos o mm por cm; un
# redondeo no es un factor limpio. La tolerancia es estrecha a propósito.
FACTORES_CONVERSION = (10.0, 100.0, 1000.0)
TOLERANCIA_CONVERSION = 0.05
# Sin esta masa crítica la mediana no es una distribución, es una anécdota.
MIN_PRODUCTOS_CONVERSION = 4
# El precio NO es una magnitud física: una diferencia de ×10 en precio es un
# cambio de categoría de producto, no una conversión de unidad (Eje 7, no 4).
# ponytail: se excluye por NOMBRE porque las fichas no traen el frontend_input;
# un tenant con códigos de precio propios exige pasar a evaluar con
# frontend_input='price' (el patrón de filter_blind).
CODIGOS_PRECIO = frozenset({"price", "special_price", "cost", "msrp", "map", "minimal_price"})


def _factor_limpio(ratio: float) -> float | None:
    """El factor (10/100/1000) si `ratio` es un múltiplo o submúltiplo limpio
    de una conversión de unidad; `None` si no hay conversión evidente."""
    if ratio <= 0:
        return None
    for factor in FACTORES_CONVERSION:
        for r in (ratio, 1.0 / ratio):
            if abs(r - factor) <= TOLERANCIA_CONVERSION * factor:
                return factor
    return None


def sospecha_conversion(fichas: Sequence[Ficha]) -> Resultado:
    """Sospecha de conversión de unidad (spec §6.1 eje 4).

    «Un peso mal cargado no es un defecto de calidad: es un flete mal cotizado.»
    Sale `candidato` a propósito: confirmar exige evidencia adicional (ficha del
    fabricante, un campo hermano coherente), no limpiar el factor a ciegas.
    """
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    por_set: dict[int | None, list[Ficha]] = defaultdict(list)
    for f in evaluables:
        por_set[f.attribute_set_id].append(f)

    # La mediana por (set, atributo) es la base de comparación. Un atributo es
    # magnitud sólo si el 80% de sus valores son numéricos (value_stats decide,
    # porque Magento declara casi todo `text`) y hay masa crítica.
    medianas: dict[tuple, float] = {}
    for set_id, grupo in por_set.items():
        codigos = sorted(
            {c for f in grupo for c in f.attributes if c not in CODIGOS_PRECIO}
        )
        for codigo in codigos:
            valores = [f.valor(codigo) for f in grupo if f.valor(codigo) is not None]
            if len(valores) < MIN_PRODUCTOS_CONVERSION:
                continue
            stats = value_stats(valores, "text")
            if stats.kind == "numerico" and stats.p50 not in (None, 0):
                medianas[(set_id, codigo)] = stats.p50

    hallazgos: list[Hallazgo] = []
    for f in evaluables:
        for codigo in f.attributes:
            mediana = medianas.get((f.attribute_set_id, codigo))
            if mediana is None:
                continue
            texto = f.valor(codigo)
            lectura = parse_number(texto)
            if lectura.motivo != "leido":
                continue
            factor = _factor_limpio(lectura.valor / mediana)
            if factor is not None:
                hallazgos.append(
                    Hallazgo(
                        "sospecha_conversion", 4, CANDIDATO, "producto", f.sku,
                        {
                            "attribute": codigo,
                            "valor": texto,
                            "mediana": mediana,
                            "factor": factor,
                            "lectura": (
                                f"«{texto}» es ~×{int(factor)} la mediana del tipo; "
                                "posible conversión de unidad (gramos por kilos, "
                                "milímetros por centímetros)"
                            ),
                        },
                    )
                )

    # ponytail: la cobertura cuenta «publicado» como el resto de los candidato,
    # aunque un publicado sin magnitud no pueda nunca disparar. Un tenant que no
    # cargue magnitudes sobre-declara cobertura; el arreglo es excluir por
    # frontend_input (el patrón de filter_blind), no más blocklists.
    return Resultado(
        hallazgos,
        Cobertura(
            "sospecha_conversion",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


# Eje 1: el nombre no sigue la plantilla de su tipo (spec §6.2). La plantilla
# es «qué atributos aparecen en los nombres bien formados»: si el valor de un
# atributo está metido en ≥70% de los nombres que lo llevan, va en el nombre.
UMBRAL_PLANTILLA = 0.70
MIN_PRODUCTOS_PLANTILLA = 4
# Valores más cortos (códigos, «si»/«no», el «1» de status) no son palabras que
# una persona ponga en un nombre: excluirlos evita el falso positivo de que un
# código corto coincida por casualidad con una sílaba del nombre.
LARGO_MINIMO_VALOR = 4


def nombre_fuera_de_plantilla(fichas: Sequence[Ficha]) -> Resultado:
    """Nombre que omite un atributo que su tipo siempre mete en el nombre.

    «Aire Acondicionado Inverter Wifi 18.000 BTU» es tipo + tecnología +
    conectividad + capacidad. Un nombre que omite la capacidad de su tipo se
    pierde en el buscador; se marca candidato, no veredicto. Solo se detecta el
    *orden* de presencia, no el orden entre sí (eso es un parser, spec §6.3).
    """
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    por_set: dict[int | None, list[Ficha]] = defaultdict(list)
    for f in evaluables:
        por_set[f.attribute_set_id].append(f)

    plantillas: dict[int | None, list[str]] = {}
    for set_id, grupo in por_set.items():
        if len(grupo) < MIN_PRODUCTOS_PLANTILLA:
            continue
        codigos = sorted({c for f in grupo for c in f.attributes if c != "name"})
        plantilla = []
        for codigo in codigos:
            con_valor = [
                f for f in grupo
                if (v := f.valor(codigo)) is not None and len(v) >= LARGO_MINIMO_VALOR
            ]
            if len(con_valor) < MIN_PRODUCTOS_PLANTILLA:
                continue
            en_nombre = sum(
                1 for f in con_valor
                if normalizar_nombre(f.valor(codigo))
                in normalizar_nombre(f.valor("name") or "")
            )
            if en_nombre / len(con_valor) >= UMBRAL_PLANTILLA:
                plantilla.append(codigo)
        if plantilla:
            plantillas[set_id] = plantilla

    hallazgos: list[Hallazgo] = []
    for f in evaluables:
        plantilla = plantillas.get(f.attribute_set_id)
        if not plantilla:
            continue
        nombre = normalizar_nombre(f.valor("name") or "")
        faltan = [
            c for c in plantilla
            if (v := f.valor(c)) is not None and len(v) >= LARGO_MINIMO_VALOR
            and normalizar_nombre(v) not in nombre
        ]
        if faltan:
            hallazgos.append(
                Hallazgo(
                    "nombre_fuera_de_plantilla", 1, CANDIDATO, "producto", f.sku,
                    {
                        "faltan": faltan,
                        "plantilla": plantilla,
                        "lectura": (
                            "el nombre no incluye el valor de " + ", ".join(faltan)
                            + ", que es parte de la plantilla de su tipo"
                        ),
                    },
                )
            )

    # ponytail: misma cobertura «publicado» que el resto de los candidato. Un
    # publicado en un set sin plantilla inferible (poco numeroso o sin atributos
    # consistentes) no puede disparar pero cuenta como evaluado.
    return Resultado(
        hallazgos,
        Cobertura(
            "nombre_fuera_de_plantilla",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


# Eje 1: basura en el nombre (spec §6.1). «El nombre dice algo que no es un
# nombre»: una etiqueta HTML, un SKU interno, signos repetidos. Todo objetivable
# con un regex, así que no es candidato: es defecto real de baja severidad.
_HTML = re.compile(r"<[^>]*>|&[a-zA-Z]+;")
_ESPACIOS_DOBLES = re.compile(r" {2,}")
_CARACTER_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PUNTUACION = re.compile(r"[!?¡¿*#]{2,}")
_DIGITO = re.compile(r"\d")


def _basura_en_nombre(nombre: str, sku: str) -> list[str]:
    """Los tipos de basura que trae el nombre. `sku` es el código interno."""
    motivos = []
    if _HTML.search(nombre):
        motivos.append("html")
    if _ESPACIOS_DOBLES.search(nombre):
        motivos.append("espacios")
    if _CARACTER_CONTROL.search(nombre):
        motivos.append("control")
    if _PUNTUACION.search(nombre):
        motivos.append("puntuacion")
    # Un SKU es un código (lleva dígito), no una palabra: "Nox" es marca, no
    # código. Y si el nombre ES el sku, es el detector vecino («el nombre es un
    # código»), no un SKU embebido.
    s = normalizar_nombre(sku) if sku else ""
    n = normalizar_nombre(nombre)
    if s and len(s) >= 4 and _DIGITO.search(s) and s in n and s != n:
        motivos.append("sku")
    return motivos


def nombre_con_basura(fichas: Sequence[Ficha]) -> Resultado:
    """Nombre con basura: HTML, dobles espacios, caracteres de control, signos
    repetidos o el SKU interno embebido. No corrige, solo señala."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos: list[Hallazgo] = []
    for f in evaluables:
        nombre = f.valor("name") or ""
        motivos = _basura_en_nombre(nombre, f.sku)
        if motivos:
            hallazgos.append(
                Hallazgo(
                    "nombre_con_basura", 1, BAJA, "producto", f.sku,
                    {
                        "motivo": motivos,
                        "nombre": nombre,
                        "lectura": "basura en el nombre: " + ", ".join(motivos),
                    },
                )
            )
    return Resultado(
        hallazgos,
        Cobertura(
            "nombre_con_basura",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


# Eje 1: el nombre es un código (spec §6.1). `INV-WIFI89283942` en el campo
# nombre: el storefront muestra el código interno en vez de un nombre humano.
# Determinista por construcción: solo marca nombre == sku. No intenta adivinar
# si un nombre «parece» un código (eso FP con modelos legítimos como CBR600RR).
def nombre_es_codigo(fichas: Sequence[Ficha]) -> Resultado:
    """El nombre es el código interno, no un nombre. No corrige, solo señala."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos: list[Hallazgo] = []
    for f in evaluables:
        nombre = f.valor("name") or ""
        n = normalizar_nombre(nombre)
        s = normalizar_nombre(f.sku) if f.sku else ""
        # Un código lleva dígito: «Paleta» como sku es una palabra, no un código.
        # Y nombre == sku no es «sku embebido» (ese es el detector vecino).
        if s and n and s == n and len(s) >= 4 and _DIGITO.search(s):
            hallazgos.append(
                Hallazgo(
                    "nombre_es_codigo", 1, MEDIA, "producto", f.sku,
                    {
                        "nombre": nombre,
                        "sku": f.sku,
                        "lectura": "el nombre es el código interno " + f.sku,
                    },
                )
            )
    return Resultado(
        hallazgos,
        Cobertura(
            "nombre_es_codigo",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


# Eje 3: valores basura (spec §6.1). Un atributo que no está vacío sino que trae
# un comodín de carga —«N/A», «-», «SIN DATO», «.», «xx»—: el dato no existe pero
# el front mostrará el comodín como si fuera un valor. `0` no es basura por sí
# mismo (puede ser válido), así que no entra acá. Solo los tokens del spec;
# ampliar cuando el catálogo real muestre más comodines.
_VALORES_BASURA = frozenset({"n/a", "-", ".", "xx", "sin dato", "sindato"})


def campos_basura(fichas: Sequence[Ficha]) -> Resultado:
    """Atributos cuyo valor es un comodín de carga. Un hallazgo por (producto,
    atributo): dos campos basura son dos causas, no una contada dos veces."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    hallazgos: list[Hallazgo] = []
    for f in evaluables:
        for codigo, valor in f.attributes.items():
            v = normalizar_nombre(str(valor))
            if v in _VALORES_BASURA:
                hallazgos.append(
                    Hallazgo(
                        "campos_basura", 3, BAJA, "producto", f.sku,
                        {
                            "atributo": codigo,
                            "valor": str(valor),
                            "lectura": f"el atributo {codigo} trae el comodín «{valor}»",
                        },
                    )
                )
    return Resultado(
        hallazgos,
        Cobertura(
            "campos_basura",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


# Eje 1: marca inconsistente (spec §6.1). La misma marca escrita de dos formas
# («Samsung» y «Sansung») en el atributo de marca: rompe el agrupado por marca en
# la tienda y en los feeds. Sale candidato: decidir cuál grafía es la canónica es
# una decisión del dueño, no del motor. Solo ataca texto libre — si la marca es
# un select con `option_id`, la inconsistencia vive en las etiquetas de opción
# (consolidación, Eje 3), no en el valor del producto.
ATRIBUTOS_MARCA = ("manufacturer", "marca", "brand", "fabricante")
# ponytail: umbral sin calibrar; más laxo que duplicado porque las marcas son
# cortas (una errata en 7 letras da ratio ~0.86). Medir con el arnés de FP.
UMBRAL_MARCA = 0.85
MIN_LARGO_MARCA = 4


def marca_inconsistente(fichas: Sequence[Ficha]) -> Resultado:
    """La marca de un grupo de productos difiere en poco de una grafía más usada.

    Un hallazgo por grafía minoritaria (no por producto): mil productos con la
    marca «Sansung» son UNA causa, no mil."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    codigo = next(
        (c for c in ATRIBUTOS_MARCA if any(f.valor(c) for f in evaluables)), None
    )
    por_marca: dict[str, list[Ficha]] = defaultdict(list)
    cruda: dict[str, str] = {}
    if codigo:
        for f in evaluables:
            v = f.valor(codigo)
            if v:
                k = normalizar_nombre(v)
                por_marca[k].append(f)
                cruda.setdefault(k, v)

    # Grafías ordenadas por frecuencia: la más usada es la referencia. Una grafía
    # solo se marca si hay otra estrictamente más frecuente y casi idéntica.
    marcas = sorted(por_marca.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    hallazgos: list[Hallazgo] = []
    for a, grupo_a in marcas:
        if len(a) < MIN_LARGO_MARCA:
            continue
        for b, grupo_b in marcas:
            if b == a or len(grupo_b) <= len(grupo_a):
                continue
            if SequenceMatcher(None, a, b).ratio() >= UMBRAL_MARCA:
                skus = sorted(f.sku for f in grupo_a)
                hallazgos.append(
                    Hallazgo(
                        "marca_inconsistente", 1, CANDIDATO, "grupo",
                        cruda[a][:120],
                        {
                            "productos": len(skus),
                            "skus": skus[:MAX_SKUS_POR_GRUPO],
                            "truncado": len(skus) > MAX_SKUS_POR_GRUPO,
                            "atributo": codigo,
                            "marca": cruda[a],
                            "candidata_a": cruda[b],
                            "lectura": (
                                f"la marca «{cruda[a]}» difiere en poco de «{cruda[b]}», "
                                "la grafía más usada del catálogo"
                            ),
                        },
                    )
                )
                break
    return Resultado(
        hallazgos,
        Cobertura(
            "marca_inconsistente",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


# Eje 1: GTIN inválido (spec §6.1). «Formato y dígito de control se validan solo
# si hay valor»: sin GTIN cargado no hay hallazgo —la exigencia de que exista es
# una regla con aplicabilidad (`fabricante_asigna_gtin`), no este detector—. Un
# GTIN con formato roto o dígito de control que no cuadra es un dato mal cargado,
# no un candidato: el algoritmo GS1 decide.
ATRIBUTOS_GTIN = ("gtin", "ean", "upc", "ean13", "barcode")
LARGOS_GTIN = frozenset({8, 12, 13, 14})


def _digito_control_gtin(cuerpo: str) -> int:
    """Dígito de control GS1: pesos 3 y 1 alternados desde el último dígito."""
    total = 0
    for i, c in enumerate(reversed(cuerpo)):
        total += int(c) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10


def gtin_invalido(fichas: Sequence[Ficha]) -> Resultado:
    """GTIN con formato inválido o dígito de control que no cuadra."""
    evaluables, no_aplica, no_evaluado = _particionar(fichas, _publicado)
    codigo = next(
        (c for c in ATRIBUTOS_GTIN if any(f.valor(c) for f in evaluables)), None
    )
    hallazgos: list[Hallazgo] = []
    if codigo:
        for f in evaluables:
            v = f.valor(codigo)
            if not v:
                continue  # sin valor: no se evalúa (spec, «solo si hay valor»)
            g = v.strip()
            if not g.isdigit() or len(g) not in LARGOS_GTIN:
                motivo = "formato"
            elif _digito_control_gtin(g[:-1]) != int(g[-1]):
                motivo = "dígito de control"
            else:
                continue
            hallazgos.append(
                Hallazgo(
                    "gtin_invalido", 1, MEDIA, "producto", f.sku,
                    {
                        "atributo": codigo,
                        "gtin": g,
                        "motivo": motivo,
                        "lectura": f"el GTIN «{g}» tiene {motivo} inválido",
                    },
                )
            )
    return Resultado(
        hallazgos,
        Cobertura(
            "gtin_invalido",
            len(evaluables),
            no_aplica,
            no_evaluado,
            "variantes no navegables y deshabilitados",
        ),
    )


DETECTORES: tuple[Callable[[Sequence[Ficha]], Resultado], ...] = (
    sin_imagen,
    sin_precio,
    nombres_repetidos,
    variantes_por_talle,
    duplicado,
    sin_descripcion,
    sin_descripcion_corta,
    sin_meta_title,
    sin_categoria,
    nombre_en_mayusculas,
    sospecha_conversion,
    nombre_fuera_de_plantilla,
    nombre_con_basura,
    nombre_es_codigo,
    campos_basura,
    marca_inconsistente,
    gtin_invalido,
)


def evaluar(fichas: Sequence[Ficha]) -> list[Resultado]:
    """Corre todos los detectores en orden estable."""
    return [d(fichas) for d in DETECTORES]
