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


TEXTOS: dict[str, Texto] = {
    "sin_imagen": Texto(
        titulo="{n} productos publicados no tienen ni una foto",
        significa=(
            "Aparecen en el listado y en el buscador con el recuadro vacío. Un producto "
            "sin foto se ve, pero no se compra."
        ),
        accion="Es la lista más corta y más rentable del informe. Priorizá por lo que más se vende.",
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
    ),
    "sin_precio": Texto(
        titulo="{n} productos publicados sin precio",
        significa=(
            "Se muestran pero no se pueden comprar. No se cuentan acá los configurables, "
            "los agrupados ni los bundles: ésos toman el precio de sus variantes."
        ),
        accion="Cargar el precio o despublicarlos.",
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
    ),
    "sin_descripcion_corta": Texto(
        titulo="{n} productos sin descripción corta",
        significa="Es el texto que aparece en el listado y en los resultados de búsqueda.",
        accion="Se puede derivar de la descripción larga cuando exista.",
    ),
    "sin_meta_title": Texto(
        titulo="{n} productos sin título para buscadores",
        significa=(
            "Sin título propio, Google usa lo que encuentra. Es lo primero que ve alguien "
            "que llega desde una búsqueda."
        ),
        accion="Se puede generar desde el nombre y la marca con una regla, no de a uno.",
    ),
    "sin_categoria": Texto(
        titulo="{n} productos publicados no cuelgan de ninguna categoría",
        significa=(
            "Existen y se pueden comprar por enlace directo, pero no se llega a ellos "
            "navegando la tienda."
        ),
        accion="Asignarles categoría.",
    ),
    "nombre_repetido": Texto(
        titulo="{n} grupos de productos comparten el mismo nombre",
        significa=(
            "Pueden ser duplicados o dos productos legítimamente distintos. Se presentan "
            "como candidatos a revisar, nunca como duplicados confirmados."
        ),
        accion="Revisar cada grupo y decidir: unificar, renombrar, o dejar como están.",
        unidad="grupos",
    ),
    "variantes_sueltas": Texto(
        titulo="{n} grupos de variantes publicadas por separado",
        significa="Varios productos idénticos en nombre que parecen variantes del mismo modelo.",
        accion="Agruparlos bajo un producto configurable.",
        unidad="grupos",
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
