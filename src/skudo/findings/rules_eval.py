"""El motor de evaluación de reglas: una regla del snapshot + fichas → Resultado.

Produce el MISMO par (hallazgos, cobertura) que los detectores de catalog.py, así
que enchufa en el flujo de run.py sin cambiarlo. Los cuatro estados salen de
profile/states.py —la misma función que el perfilador y S1b— para que
`desconocido` no se confunda nunca con carencia.

La severidad de cada hallazgo es SIEMPRE `regla.severity`, nunca una constante
fija en este módulo: `ReglaEvaluable.severity` ya trae la severidad EFECTIVA
resuelta por quien arma la regla (Task 3) —`aviso` (peso 0) para una regla en
estado `aviso`, la severidad curada para una regla aceptada—. Si este motor
hardcodeara `CANDIDATO` para rango/formato, una regla `aviso` penalizaría
igual que una aceptada, violando la restricción global "solo las reglas
aceptadas penalizan".
"""

from dataclasses import dataclass

from skudo.findings.catalog import (
    Cobertura,
    Ficha,
    Hallazgo,
    Resultado,
    _publicado,
)
from skudo.profile.states import State, attribute_state


@dataclass(frozen=True)
class ReglaEvaluable:
    """Lo que el motor necesita de una regla, ya resuelto desde la fila."""

    id: int
    kind: str
    axis: int
    severity: str
    scope_kind: str
    scope_key: str
    store_view: int | None
    definition: dict


def codigo_de(kind: str, attribute: str) -> str:
    """El código de un hallazgo de regla: prefijo `regla:` y agrupado por atributo."""
    return f"regla:{kind}:{attribute}"


def _scope_estado(regla: ReglaEvaluable, f: Ficha) -> str:
    """Si la ficha cae en el scope de la regla: "en_scope", "fuera" o
    "desconocido". `category` se resuelve en run.py (necesita las
    asignaciones), así que aquí una regla de categoría se considera aplicable
    y run.py la acota antes de llamar.

    Un `attribute_set_id` ausente no es "fuera de scope": es que no sabemos si
    cae dentro. Contarlo como "fuera" (no_aplica) sería la misma trampa que
    `profile/states.py` prohíbe para el valor del atributo —la ignorancia
    disfrazada de veredicto—, así que acá también gana la ignorancia y va a
    no_evaluado, nunca a no_aplica ni a un hallazgo.
    """
    if regla.scope_kind == "global":
        return "en_scope"
    if regla.scope_kind == "subtype":
        # `scope_key` de un subtipo es un VALOR de divisor (ej. "ropa"), no un
        # attribute_set_id: comparalo contra attribute_set_id normalmente no
        # matchea nada, pero PODRÍA mis-fire si un valor de divisor coincide
        # numéricamente con un set id real (ej. "9"). La resolución fina de
        # subtipo está diferida (spec §3.2), así que acá gana la ignorancia:
        # nunca se compara, siempre desconocido/no_evaluado. Un subtype jamás
        # puede rendir veredicto ni mis-fire.
        return "desconocido"
    if regla.scope_kind == "attribute_set":
        if f.attribute_set_id is None:
            return "desconocido"
        return "en_scope" if str(f.attribute_set_id) == regla.scope_key else "fuera"
    if regla.scope_kind == "category":
        return "en_scope"
    return "fuera"


def _num(valor: str | None) -> float | None:
    if valor is None:
        return None
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _es_ambiguo(valor: str | None) -> bool:
    """Un valor que NO se lee como número sin adivinar: coma decimal, texto, etc."""
    if valor is None:
        return False
    s = str(valor).strip()
    if not s:
        return False
    return _num(s) is None or ("," in s)


def evaluar_regla(
    regla: ReglaEvaluable,
    fichas,
    sets_by_code: dict[str, frozenset[int]],
) -> Resultado:
    """Evalúa UNA regla sobre las fichas. Devuelve hallazgos y cobertura."""
    attribute = regla.definition["attribute"]
    code = codigo_de(regla.kind, attribute)

    hallazgos: list[Hallazgo] = []
    evaluados = no_aplica = no_evaluado = 0
    en_scope: list[Ficha] = []
    for f in fichas:
        estado_scope = _scope_estado(regla, f)
        if estado_scope == "desconocido":
            no_evaluado += 1
        elif estado_scope == "fuera":
            no_aplica += 1  # otro scope no aplica a esta regla
        else:
            en_scope.append(f)

    for f in en_scope:
        if not _publicado(f):
            no_aplica += 1
            continue
        if regla.kind == "obligatoriedad":
            estado = attribute_state(f.attributes, f.attribute_set_id, attribute, sets_by_code)
            if estado is State.DESCONOCIDO:
                no_evaluado += 1
            elif estado is State.NO_APLICA:
                no_aplica += 1
            else:
                evaluados += 1
                if estado is State.VACIO:
                    hallazgos.append(Hallazgo(code, regla.axis, regla.severity,
                                              "producto", f.sku,
                                              {"attribute": attribute, "rule_id": regla.id}))
        elif regla.kind == "rango":
            valor = _num(f.valor(attribute))
            if valor is None:
                no_aplica += 1
                continue
            evaluados += 1
            lo, hi = regla.definition.get("min"), regla.definition.get("max")
            if (lo is not None and valor < lo) or (hi is not None and valor > hi):
                hallazgos.append(Hallazgo(code, regla.axis, regla.severity, "producto", f.sku,
                                          {"attribute": attribute, "rule_id": regla.id,
                                           "valor": valor, "min": lo, "max": hi}))
        elif regla.kind == "formato":
            crudo = f.valor(attribute)
            if crudo is None:
                no_aplica += 1
                continue
            evaluados += 1
            if regla.definition.get("sospecha_conversion") and _es_ambiguo(crudo):
                hallazgos.append(Hallazgo(code, regla.axis, regla.severity, "producto", f.sku,
                                          {"attribute": attribute, "rule_id": regla.id,
                                           "valor": str(crudo)}))
        else:
            no_aplica += 1  # kind no evaluable en S1c-1 (plantilla_nombre, unidad, filtrable)

    return Resultado(
        hallazgos,
        Cobertura(code, evaluados, no_aplica, no_evaluado,
                  "productos fuera del scope de la regla, no publicados, o no numéricos"),
    )
