"""Filter-blind: un atributo FILTRABLE vacío esconde el producto del filtrado.

Spec §6.1 eje 3: "atributo filtrable vacío ⇒ el producto no aparece al
filtrar. El hallazgo con traducción más directa a dinero perdido." Es universal
—vale para todo atributo filtrable, no requiere una regla curada— así que vive
como detector, no como regla; pero necesita el catálogo de atributos
(cuáles son filtrables, a qué sets pertenecen), que las fichas no traen, así
que lo evalúa `run.py` con `sets_by_code`, igual que el motor de reglas.

Se apoya en `attribute_state` —la MISMA función del perfilador y las reglas—
para que 'vacío' nunca se confunda con 'no aplica' ni con 'desconocido': marcar
un atributo que no pertenece al set del producto sería un filtro-ciego
fabricado.
"""

from collections.abc import Iterable, Sequence

from skudo.findings.catalog import MEDIA, Cobertura, Ficha, Hallazgo, Resultado, _publicado
from skudo.profile.states import State, attribute_state

CODE_PREFIX = "filtro_ciego"
# Eje 3 (atributos), igual que la obligatoriedad.
AXIS = 3


def codigo_de(attribute: str) -> str:
    return f"{CODE_PREFIX}:{attribute}"


def evaluar_filtro_ciego(
    fichas: Sequence[Ficha],
    filtrables: Iterable[str],
    sets_by_code: dict[str, frozenset[int]],
) -> list[Resultado]:
    """Un Resultado por atributo filtrable. El código agrupa por atributo
    (`filtro_ciego:color`), evaluado sobre TODOS sus sets en una sola pasada,
    así que dos sets con el mismo atributo no colisionan en la cobertura."""
    resultados: list[Resultado] = []
    for attribute in sorted(set(filtrables)):
        code = codigo_de(attribute)
        hallazgos: list[Hallazgo] = []
        evaluados = no_aplica = no_evaluado = 0
        for f in fichas:
            if not _publicado(f):
                no_aplica += 1  # un producto que no está en vitrina no se filtra
                continue
            estado = attribute_state(f.attributes, f.attribute_set_id, attribute, sets_by_code)
            if estado is State.NO_APLICA:
                no_aplica += 1
            elif estado is State.DESCONOCIDO:
                no_evaluado += 1
            else:
                evaluados += 1
                if estado is State.VACIO:
                    hallazgos.append(
                        Hallazgo(code, AXIS, MEDIA, "producto", f.sku, {"attribute": attribute})
                    )
        resultados.append(
            Resultado(
                hallazgos,
                Cobertura(
                    code, evaluados, no_aplica, no_evaluado,
                    "no publicados, o el atributo no aplica al set del producto",
                ),
            )
        )
    return resultados
