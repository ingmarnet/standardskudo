"""El texto humano de cada detector.

Vive separado del detector a propósito: el detector decide *qué* es un
hallazgo y este módulo decide *cómo se le cuenta a alguien que no programa*.
Mezclarlos haría que cambiar una palabra del informe exija tocar la lógica de
detección.

`tests/report/test_textos.py` exige que TODO detector tenga su texto. Un
hallazgo sin texto no se pierde —se muestra con su código crudo— pero el test
falla, que es cómo nos enteramos de que falta escribirlo.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Texto:
    titulo: str
    significa: str
    accion: str
    unidad: str = "productos"
    # Un hallazgo solo es un caso común —el último que queda por corregir— y
    # «1 productos» delata que el informe lo escribió una máquina sin leerlo.
    singular: str = ""

    def encabezado(self, n: int, formatear) -> str:
        plantilla = self.singular if n == 1 and self.singular else self.titulo
        return plantilla.format(n=formatear(n))


TEXTOS: dict[str, Texto] = {
    "sin_imagen": Texto(
        titulo="{n} productos publicados no tienen ni una foto",
        significa=(
            "Aparecen en el listado y en el buscador con el recuadro vacío. Un producto "
            "sin foto se ve, pero no se compra."
        ),
        accion="Es la lista más corta y más rentable del informe. Priorizá por lo que más se vende.",
        singular="Un producto publicado no tiene ni una foto",
    ),
    "variantes_por_talle": Texto(
        titulo="{n} familias de talles publicadas como productos sueltos",
        significa=(
            "Cada talle es una ficha separada, con el talle metido dentro del nombre. "
            "Quien busca el modelo recibe muchas filas casi iguales en vez de un producto "
            "con selector de talle."
        ),
        accion=(
            "Agrupar cada familia bajo un producto configurable con el talle como variación. "
            "El efecto se ve de inmediato en el listado y en el buscador."
        ),
        unidad="familias",
        singular="Una familia de talles publicada como productos sueltos",
    ),
    "sin_precio": Texto(
        titulo="{n} productos publicados sin precio",
        significa=(
            "Se muestran pero no se pueden comprar. No se cuentan acá los configurables, "
            "los agrupados ni los bundles: ésos toman el precio de sus variantes."
        ),
        accion="Cargar el precio o despublicarlos.",
        singular="Un producto publicado no tiene precio",
    ),
    "sin_descripcion": Texto(
        titulo="{n} productos sin descripción",
        significa=(
            "El producto no explica qué es, ni al comprador que duda ni al buscador que "
            "decide si mostrarlo."
        ),
        accion=(
            "Empezar por los que además venden. Buena parte se puede redactar desde los "
            "atributos que el producto ya tiene cargados."
        ),
        singular="Un producto no tiene descripción",
    ),
    "sin_descripcion_corta": Texto(
        titulo="{n} productos sin descripción corta",
        significa="Es el texto que aparece en el listado y en los resultados de búsqueda.",
        accion="Se puede derivar de la descripción larga cuando exista.",
        singular="Un producto no tiene descripción corta",
    ),
    "sin_meta_title": Texto(
        titulo="{n} productos sin título para buscadores",
        significa=(
            "Sin título propio, Google usa lo que encuentra. Es lo primero que ve alguien "
            "que llega desde una búsqueda."
        ),
        accion="Se puede generar desde el nombre y la marca con una regla, no de a uno.",
        singular="Un producto no tiene título para buscadores",
    ),
    "sin_categoria": Texto(
        titulo="{n} productos publicados no cuelgan de ninguna categoría",
        significa=(
            "Existen y se pueden comprar por enlace directo, pero no se llega a ellos "
            "navegando la tienda."
        ),
        accion="Asignarles categoría.",
        singular="Un producto publicado no cuelga de ninguna categoría",
    ),
    "nombre_repetido": Texto(
        titulo="{n} grupos de productos comparten el mismo nombre",
        significa=(
            "Pueden ser duplicados o dos productos legítimamente distintos. Se presentan "
            "como candidatos a revisar, nunca como duplicados confirmados."
        ),
        accion="Revisar cada grupo y decidir: unificar, renombrar, o dejar como están.",
        unidad="grupos",
        singular="Un grupo de productos comparte el mismo nombre",
    ),
    "duplicado": Texto(
        titulo="{n} grupos de productos con nombres casi idénticos",
        significa=(
            "Difieren en poco —una errata, un plural, «rojo» por «roja»— y bien pueden "
            "ser el mismo producto cargado dos veces. Se presentan como candidatos a "
            "revisar, nunca como duplicados confirmados."
        ),
        accion=(
            "Revisar cada grupo contra capacidad, color, revisión y presentación antes de "
            "unificar: dos registros parecidos pueden ser dos productos distintos."
        ),
        unidad="grupos",
        singular="Un grupo de productos tiene nombres casi idénticos",
    ),
    "variantes_sueltas": Texto(
        titulo="{n} grupos de variantes publicadas por separado",
        significa="Varios productos idénticos en nombre que parecen variantes del mismo modelo.",
        accion="Agruparlos bajo un producto configurable.",
        unidad="grupos",
        singular="Un grupo de variantes publicadas por separado",
    ),
    "nombre_en_mayusculas": Texto(
        titulo="{n} nombres están íntegramente en MAYÚSCULAS",
        significa=(
            "A este nivel no es un descuido: es la convención con la que se carga el "
            "catálogo. Se reporta porque tiene efecto real —las mayúsculas se leen más "
            "lento— pero la decisión es de la tienda."
        ),
        accion=(
            "Decidir la convención. Si se cambia, se cambia con una regla sobre todo el "
            "catálogo, no producto por producto."
        ),
        singular="Un nombre está íntegramente en MAYÚSCULAS",
    ),
    "sospecha_conversion": Texto(
        titulo="{n} productos con un valor fuera de escala para su tipo",
        significa=(
            "Un valor ×10, ×100 o ×1000 la mediana de sus pares suele ser una unidad "
            "cargada mal: gramos por kilos, milímetros por centímetros. Es un candidato "
            "a revisar, no una corrección automática."
        ),
        accion=(
            "Confirmar contra la ficha del fabricante antes de tocar nada: un precio o una "
            "presentación pueden ser legítimamente mayores."
        ),
        singular="Un producto tiene un valor fuera de escala para su tipo",
    ),
    "nombre_fuera_de_plantilla": Texto(
        titulo="{n} productos cuyo nombre no sigue la plantilla de su tipo",
        significa=(
            "El nombre omite un atributo que los nombres bien formados de su tipo "
            "siempre incluyen (la capacidad en un aire acondicionado, la marca en "
            "una paleta). Se pierde en el buscador. Es un candidato a revisar, no "
            "una corrección automática."
        ),
        accion=(
            "Completar el nombre desde los atributos que el producto ya tiene cargados: "
            "no falta el dato, falta meterlo en el nombre."
        ),
        singular="Un producto tiene un nombre fuera de la plantilla de su tipo",
    ),
    "nombre_con_basura": Texto(
        titulo="{n} productos con basura en el nombre",
        significa=(
            "El nombre trae HTML, dobles espacios, caracteres de control, signos "
            "repetidos o el código interno embebido. Se ve roto en el listado y el "
            "buscador no lo lee como texto."
        ),
        accion="Limpiar el nombre: es un reemplazo directo, no una decisión de catálogo.",
        singular="Un producto tiene basura en el nombre",
    ),
    "nombre_es_codigo": Texto(
        titulo="{n} productos muestran el código interno como nombre",
        significa=(
            "En vez de un nombre humano, el producto se llama con su SKU. Quien "
            "busca no encuentra, y el listado muestra una cadena que solo tiene "
            "sentido para el que cargó el catálogo."
        ),
        accion=(
            "Redactar el nombre desde los atributos que el producto ya tiene: no "
            "falta el dato, falta usarlo en el nombre."
        ),
        singular="Un producto muestra el código interno como nombre",
    ),
    "campos_basura": Texto(
        titulo="{n} campos traen un comodín de carga en vez de un valor",
        significa=(
            "Un atributo que no está vacío pero dice «N/A», «-», «SIN DATO», "
            "«.» o «xx». No es un dato: es el hueco que dejó la carga. Se ve en "
            "el listado como si fuera un valor real y ensucia los filtros."
        ),
        accion=(
            "Reemplazar el comodín por el valor real o, si no existe, vaciar el "
            "campo. Un cero no es un comodín: es un valor y se queda."
        ),
        unidad="campos",
        singular="Un campo trae un comodín de carga en vez de un valor",
    ),
    "marca_inconsistente": Texto(
        titulo="{n} grafías distintas de una misma marca",
        significa=(
            "La misma marca aparece escrita de más de una forma —«Samsung» junto "
            "a «Sansung»—. La tienda la agrupa como dos marcas distintas y el "
            "comprador que filtra por una no ve los productos de la otra. Es un "
            "candidato a revisar: decidir la grafía canónica es del dueño."
        ),
        accion=(
            "Elegir una grafía por marca y unificar las demás con una regla, no "
            "producto por producto."
        ),
        unidad="grafías",
        singular="Una grafía distinta de una marca ya existente",
    ),
    "gtin_invalido": Texto(
        titulo="{n} productos con un GTIN mal cargado",
        significa=(
            "El GTIN tiene un formato que no corresponde (no son solo dígitos, o "
            "su largo no es 8, 12, 13 o 14) o su dígito de control no cuadra con "
            "el resto. Un GTIN mal cargado no identifica al producto en Google "
            "Shopping ni en los marketplaces."
        ),
        accion=(
            "Corregir contra la ficha o el envase del producto. El dígito de "
            "control se calcula: no es un dato que haya que adivinar."
        ),
        singular="Un producto tiene un GTIN mal cargado",
    ),
    "valores_negativos": Texto(
        titulo="{n} valores negativos en campos que no pueden serlo",
        significa=(
            "Un precio, un peso o una dimensión negativos no existen: rompen el "
            "cálculo de envío y el checkout. Es un error de carga, no una "
            "decisión de catálogo."
        ),
        accion="Corregir el signo o vaciar el campo contra el dato real.",
        singular="Un valor negativo en un campo que no puede serlo",
    ),
    "configurable_sin_hijos": Texto(
        titulo="{n} productos configurables sin variantes",
        significa=(
            "Un configurable no se vende a sí mismo: se venden sus variantes. "
            "Sin ninguna variante que lo reclame como padre, el producto se "
            "muestra pero no hay qué agregar al carrito."
        ),
        accion=(
            "Crear las variantes que le correspondan y vincularlas al "
            "configurable, o despublicarlo si todavía no está listo."
        ),
        singular="Un producto configurable no tiene variantes",
    ),
    "variantes_sin_atributos_de_variacion": Texto(
        titulo="{n} configurables con variantes que no comparten el eje de variación",
        significa=(
            "Un configurable varía por un conjunto de atributos (color, talle…). "
            "Si una de sus variantes no informa el valor de uno de esos ejes, "
            "queda un hueco en el selector y esa variante no se puede elegir."
        ),
        accion=(
            "Completar el valor del atributo que falta en cada variante, o "
            "quitar ese eje de la configuración si no aplica al producto."
        ),
        singular="Un configurable tiene variantes que no comparten un eje de variación",
    ),
    "unidades_mezcladas": Texto(
        titulo="{n} atributos que mezclan unidades",
        significa=(
            "Un atributo que mezcla unidades (kilos y gramos, litros y "
            "mililitros…) vuelve incomparables sus valores: rompe el orden y el "
            "filtro, y en peso o dimensiones cotiza mal el envío."
        ),
        accion=(
            "Unificar el atributo en una sola unidad canónica y recargar los "
            "valores, o dividirlo en atributos distintos si son magnitudes distintas."
        ),
        singular="Un atributo mezcla dos o más unidades",
    ),
    "unidades_ausentes": Texto(
        titulo="{n} atributos con valores sin unidad",
        significa=(
            "Dentro del mismo atributo, unos valores llevan unidad y otros van "
            "pelados: un peso con `500` al lado de `1 kg` es un peso sin unidad, "
            "y quien cotiza un flete no sabe si son gramos o kilos."
        ),
        accion=(
            "Completar la unidad en los valores que la omiten, o dividir el "
            "atributo si mezcla magnitudes distintas."
        ),
        singular="Un atributo tiene valores con y sin unidad",
    ),
    "descripcion_corta_copia_larga": Texto(
        titulo="{n} productos con la descripción corta copiada de la larga",
        significa=(
            "La descripción corta debe resumir la larga para el grid y el "
            "comparador. Copiada literal, duplica contenido y excede el espacio "
            "donde se muestra sin aportar nada."
        ),
        accion="Escribir un resumen breve, o dejarla vacía si no hay nada que resumir.",
        singular="La descripción corta es una copia literal de la larga",
    ),
    "texto_duplicado": Texto(
        titulo="{n} descripciones compartidas en masa",
        significa=(
            "La misma descripción pegada en decenas de productos no distingue "
            "el producto de sus hermanos y es la firma del copy-paste de carga. "
            "Diluye el contenido para el buscador y para el comprador."
        ),
        accion=(
            "Reescribir una descripción propia por producto, o bajar el umbral "
            "si el catálogo comparte texto legítimo entre variantes."
        ),
        singular="Una descripción está duplicada en decenas de productos",
    ),
    "meta_duplicado": Texto(
        titulo="{n} meta títulos o descripciones compartidos en masa",
        significa=(
            "El mismo meta title o meta description en decenas de productos "
            "hace que compitan entre sí en el buscador y no describan a "
            "ninguno. Es un aviso clásico de Search Console."
        ),
        accion=(
            "Escribir un meta title y description únicos por producto, o bajar "
            "el umbral si el catálogo templa estos campos a propósito."
        ),
        singular="Un meta title o description está duplicado en decenas de productos",
    ),
    "alt_text": Texto(
        titulo="{n} productos con alt text ausente o de relleno",
        significa=(
            "La imagen tiene texto alternativo ausente o igual al nombre del "
            "archivo — lo que Magento auto-rellena al subir sin editarlo. Un alt "
            "de relleno no describe la imagen ni para el lector de pantalla ni "
            "para el buscador."
        ),
        accion="Escribir un alt text que describa el producto de la foto.",
        singular="Un producto tiene alt text ausente o igual al archivo",
    ),
}


def texto_de(code: str) -> Texto:
    """El texto de un detector, o uno genérico que no esconde el hallazgo."""
    return TEXTOS.get(
        code,
        Texto(
            titulo="{n} hallazgos de tipo «" + code + "»",
            significa="Este detector todavía no tiene una explicación escrita.",
            accion="Revisar el detalle.",
        ),
    )
