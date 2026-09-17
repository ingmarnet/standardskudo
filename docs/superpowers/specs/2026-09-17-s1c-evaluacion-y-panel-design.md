# S1c-1 — Evaluación por reglas y su panel: diseño

**Creado:** 2026-09-17 · **Depende de:**
`2026-09-11-s1-perfilador-design.md` §7 (define S1c) y el spec maestro
`2026-09-08-...-design.md` §6.4 (puntuación), que son la autoridad. También depende de
S1b (`src/skudo/rules/`, ya en `main`), de los detectores de S0 (`src/skudo/findings/`) y
del scoring (`src/skudo/score/`), todos construidos.
**Estado:** en diseño. Aprobado en brainstorming el 2026-09-17. Sin código.

---

## 1. Qué decide este documento

El spec define S1c como *"motor determinista puro: reglas + espejo → hallazgos"* con ~10
detectores, cuatro estados, cobertura, errores críticos, dedup por causa raíz, PHS y grado,
y un arnés de falsos positivos. Es demasiado para un corte. Este documento define el
**primer corte, S1c-1**, decidido en brainstorming:

- **El motor que convierte reglas ACEPTADAS (obligatoriedad, rango, formato) + espejo →
  hallazgos**, unificado con los detectores especiales que ya existen.
- **La atribución del hallazgo a la regla** que lo produjo, con la versión del ruleset.
- **Su superficie en el panel**: hallazgos por regla y una vista de reglas en lectura.

Fuera de S1c-1, para cortes siguientes: duplicados, sets muertos, plantilla de nombre,
unidades, categorías sin mapeo externo, y el arnés de medición de falsos positivos. Nada de
IA ni sonda (eso es S6/S5).

Norma del proyecto que gobierna este corte: **cada avance se ve en el panel**
([[skudo-avances-visibles-en-panel]]), no sólo en el CLI.

---

## 2. Las dos decisiones cerradas en brainstorming

### 2.1 Las reglas son ADITIVAS, no reemplazan a los detectores

Los detectores de S0 (`sin_imagen` con su centinela `no_selection`, `sin_precio` con la
herencia de configurables, `variantes_por_talle`, `nombre_en_mayusculas`,
`nombres_repetidos`) **se quedan como están**. Codifican lecciones de falsos positivos
—medidas contra el catálogo real— que una regla genérica no expresa. El motor de reglas
**agrega** cobertura para atributos que esos detectores no miran.

La alternativa —migrar los detectores genéricos a reglas— se descartó por dos razones: una
regla no puntúa hasta que alguien la acepta, así que migrar regresaría el panel a cero
hallazgos hasta curar; y descartaría lógica probada. El costo de lo aditivo es un posible
solapamiento (un atributo mirado por un detector y por una regla), que la **dedup por causa
raíz** (§5) resuelve.

### 2.2 `rango` y `formato` son `candidato`, no defecto duro

Un valor numérico fuera de `[p05, p95]` es una **sospecha de implausibilidad**, no una
certeza: el p95 deja fuera al 5 % legítimo por construcción. Un hallazgo de `rango` o de
`formato` (sospecha de conversión) sale con severidad `candidato` —exige revisión humana,
pesa poco (3 puntos), nunca se presenta como veredicto—. Es coherente con el peso que
`scoring.py` ya da a `candidato` y con el principio rector del spec: penalizar fuerte una
sospecha castiga al que tiene un catálogo difícil, no al que lo tiene mal.

Obligatoriedad sí es defecto real: si la evidencia dice que el 97 % de un grupo tiene el
atributo y este producto no, la carencia es real. Sale con la severidad que la regla
declare (por defecto `media`, como las carencias de campo existentes).

---

## 3. El motor de evaluación de reglas

Vive en `src/skudo/findings/rules_eval.py` y produce el **mismo par
`Resultado(hallazgos, cobertura)`** que los detectores de `catalog.py`, para enchufar en el
flujo de `run.py` sin cambiarlo por dentro.

### 3.1 La entrada: un ruleset congelado

El motor evalúa contra un `RulesetSnapshot` (S1b): el conjunto **congelado** de ids de
regla `aceptada`/`aviso` de esa store view en un instante. Puntuar contra un snapshot y no
contra "las reglas de ahora" es lo que hace el score reproducible y datable (§6.4 del
spec: *"todo score guarda la versión de ruleset"*). Si no se pasa versión, se usa el
snapshot de mayor versión de esa store view; si no hay ninguno, el motor de reglas no
produce hallazgos (y lo declara en cobertura), pero los detectores especiales sí corren.

Sólo las reglas `aceptada` producen defecto que puntúa; las `aviso` producen hallazgo que
**no** penaliza (severidad efectiva `aviso`, peso 0), tal como el spec exige. Una regla
`borrador` o `rechazada` nunca entra al snapshot, así que el motor no la ve — el candado de
S1b se hereda aquí sin código extra.

### 3.2 A qué productos aplica una regla

Una regla lleva `scope_kind` ∈ {global, attribute_set, subtype, category} y `scope_key`.
El motor mapea cada producto a las reglas que le aplican:

- `attribute_set`: el producto cuyo `attribute_set_id` == `int(scope_key)`.
- `subtype`: el producto de ese set cuyo valor de divisor == `scope_key`. El divisor lo
  dice el `ProfileRun` que generó la regla (`profile_run_id`); en S1c-1, si resolver el
  divisor exacto es costoso, se aplica al set entero y se declara en cobertura. *(Decisión
  de alcance: la resolución fina de subtipo es una mejora medible, no un bloqueo; el
  perfil ya guarda el divisor y el refinamiento entra cuando se mida que hace falta.)*
- `category`: el producto asignado a esa categoría de Magento (piso externo).
- `global`: todos los productos de la store view.

Y siempre acota por `store_view_magento_id` (NULL = todas).

### 3.3 Los tres evaluadores

Para cada regla del snapshot, sobre los productos en su scope:

| `kind` | Hallazgo cuando | Severidad | Cobertura |
|---|---|---|---|
| `obligatoriedad` | el atributo está en estado `vacio` (por `attribute_state`) | la de la regla (default `media`) | evaluados = `presente`+`vacio`; `no_evaluado` = `desconocido`; `no_aplica` = fuera del scope |
| `rango` | el atributo es numérico presente y cae fuera de `[min, max]` | `candidato` | evaluados = presentes numéricos; no numérico o vacío → `no_aplica` |
| `formato` | `definition.sospecha_conversion` y el atributo tiene valores ambiguos | `candidato` | idem rango |

Los cuatro estados salen de `profile/states.py` (`attribute_state`), **la misma función**
que el perfilador y S1b usan: `desconocido` nunca cuenta como carencia ni como cobertura,
entra sólo en `no_evaluado`. Reusar esa función —y no una copia del predicado— es lo que
impide el defecto C2 (dos definiciones de "presente") que ya mordió antes.

Cada `Hallazgo` de regla lleva en su `evidence` el `rule_id`, el `attribute`, y el valor
observado cuando aplica (para rango/formato). `subject_type = "producto"`, `subject_key =
sku`, para que `score_run._hallazgos_por_sku` lo atribuya sin cambios.

### 3.4 El código del hallazgo

Un hallazgo de regla tiene `code = f"regla:{kind}:{attribute}"` (p. ej.
`regla:obligatoriedad:color`). El prefijo `regla:` lo distingue de los detectores
especiales en el informe y en el panel, y el sufijo lo agrupa por atributo para la dedup.

---

## 4. La atribución: `Finding` gana dos columnas

Migración **0020**: `finding` gana `rule_id` (FK a `rule.id`, nullable) y `ruleset_version`
(int, nullable). Un hallazgo de detector especial los deja NULL; uno de regla los llena.
Es lo que permite que el panel diga "este hallazgo lo produjo esta regla, con esta
confianza y esta cobertura", y que un score sea trazable a la versión de ruleset con que se
calculó.

`FindingRun` gana `ruleset_version` (nullable): la versión del snapshot con que se corrió
la evaluación de reglas de esa pasada. Es el "todo score guarda la versión de ruleset" del
spec, a nivel de pasada.

Ninguna de las tablas de score cambia de forma en S1c-1: `ProductScore.run_id` ya apunta a
la pasada, y la pasada ya nombra su ruleset. Si más adelante se quiere el ruleset por
producto sin pasar por la pasada, se agrega entonces, con la medición que lo justifique.

---

## 5. Dedup por causa raíz antes de puntuar

El spec prohíbe la doble penalización: *"una misma contradicción se manifiesta en varios
ejes; se deduplica por causa raíz y se penaliza una vez"*. `scoring.py::nota_de_producto`
ya dedup por `code` (un `setdefault(h.code, h)`). S1c-1 extiende la causa raíz a
`(atributo)`:

- Un detector especial y una regla que marcan **el mismo atributo del mismo producto** son
  una causa. La clave de dedup pasa de `code` a la **causa**: para hallazgos de campo, el
  atributo (`evidence.campo` o `evidence.attribute`); para el resto, el `code`.
- Se conserva el hallazgo de **mayor severidad** de la causa (el detector especial suele
  ser más específico), y se puntúa una vez.

Esto se implementa en `scoring.py` sin tocar los detectores: la función de nota recibe
hallazgos con su evidencia y decide la causa. Un test de sabotaje afirma que un producto
marcado por `sin_descripcion` (detector) y por `regla:obligatoriedad:description` (regla)
descuenta **una** vez, no dos.

---

## 6. El CLI

`skudo evaluate --tenant X --store S [--ruleset V]`: corre una pasada de detección que
incluye los detectores especiales **y** el motor de reglas contra el snapshot V (o el
último). Escribe el `FindingRun` con su `ruleset_version`, los `Finding` con su atribución,
y puntúa (reusa `score_run`). Es el `detect_store_view` de hoy, extendido para además
evaluar reglas; el `cycle` diario lo llama igual.

El comando existente `findings` (informe) gana en su JSON la parte por regla; ver §7.

---

## 7. La superficie en el panel

La skill **impeccable** gobierna lo visual. El panel (`src/skudo/web/`) ya tiene Dashboard,
Hallazgos, Productos y Tendencia. S1c-1 agrega:

### 7.1 Endpoints nuevos (API)

- `GET /api/tenants/{code}/rules?store=S` — las reglas activas con, por cada una: kind,
  scope, atributo, confianza, evidencia, estado (aceptada/aviso), y **cuántos productos
  marca** en la última pasada (su recuento de hallazgos) y su cobertura.
- El `GET .../findings` existente gana, por hallazgo, `rule_id` y (si es de regla) la
  confianza y el eje de su regla, para que la vista los muestre.

### 7.2 Vistas nuevas / cambiadas (SPA)

- **Vista Reglas** (nueva): tabla de reglas activas ordenada por impacto (severidad ×
  productos marcados), con su confianza, su cobertura, y su origen (inferida / piso externo
  / curada). Es la lectura de lo que S1d va a hacer curable. Un badge distingue
  `aceptada` de `aviso`.
- **Vista Hallazgos** (cambiada): cada fila de hallazgo dice si vino de un **detector** o
  de una **regla**, y en el segundo caso enlaza a la regla (su confianza, su cobertura).
  Sigue ordenada por impacto, con la cobertura pegada a cada número.
- **Dashboard** (sin cambio de forma): salud/grado/distribución, ahora reflejando también
  los hallazgos de regla. Un tile nuevo: "reglas activas" (cuántas aceptadas / en aviso).

### 7.3 Datos

Hasta que el servidor alcance el Magento ([[skudo-servidor-produccion]]), el panel se ve
con **datos demo realistas de Renovapadel** que reflejan la nueva forma (hallazgos de regla
+ de detector, reglas activas). El artifact demo se actualiza; el panel en vivo (`skudo
serve`) muestra lo mismo cuando haya datos reales.

---

## 8. Estructura de archivos

```
src/skudo/findings/
  rules_eval.py     motor: ruleset_snapshot + espejo → Resultado[]; los tres evaluadores
  run.py            (mod) detect_store_view además evalúa reglas y sella ruleset_version
  models.py         (mod) Finding.rule_id, Finding.ruleset_version, FindingRun.ruleset_version
  run.py findings_report  (mod) el informe incluye la parte por regla
src/skudo/score/
  scoring.py        (mod) dedup por causa raíz (atributo), no sólo por code
src/skudo/web/
  app.py            (mod) endpoint /rules; /findings enriquecido con atribución
  static/index.html (mod) vista Reglas; vista Hallazgos con origen; tile de reglas
src/skudo/cli.py    (mod) comando `evaluate`
alembic/versions/
  0020_finding_rule_attribution.py
tests/findings/
  test_rules_eval.py
tests/score/
  test_dedup_causa_raiz.py
tests/web/
  test_rules_endpoint.py
```

Reglas de frontera:
- `rules_eval.py` lee el espejo y el snapshot; usa `attribute_state` de `profile/states.py`
  y las reglas de `rules/models.py`. No importa de `score/`.
- La dedup por causa raíz vive en `scoring.py`, un solo lugar, con su test de sabotaje.
- El panel es lectura: no cura reglas (eso es S1d). Los endpoints nuevos son GET.

---

## 9. Lo que S1c-1 NO hace

- **No cura reglas desde el panel.** Eso es S1d; el panel de S1c-1 es lectura.
- **No mide falsos positivos.** El arnés y la degradación automática por FP son un corte
  siguiente; `false_positive_rate` sigue NULL y la transición `degradar_por_fp` (ya existe
  en S1b) espera a que ese corte la dispare.
- **No agrega los detectores pesados** (duplicados, sets muertos, plantilla de nombre,
  unidades, categorías sin mapeo). Cortes siguientes.
- **No llama IA ni lee la página publicada.**
- **No resuelve el subtipo fino** si es costoso: aplica al set y lo declara en cobertura.

---

## 10. Criterios de aceptación

1. **Reglas + espejo → hallazgos, atribuidos y reproducibles.** Dos evaluaciones contra el
   mismo snapshot y la misma generación del espejo producen los mismos hallazgos, cada uno
   con su `rule_id` y el `ruleset_version` de la pasada.
2. **Sólo lo aceptado puntúa.** Una regla `aviso` produce hallazgo con peso 0; una
   `borrador`/`rechazada` no produce ninguno (no está en el snapshot). Test de sabotaje.
3. **Sin doble penalización.** Un producto marcado por un detector especial y por una regla
   sobre el mismo atributo descuenta una vez. Test de sabotaje.
4. **Cuatro estados respetados.** Un atributo `desconocido` no genera carencia y entra en
   `no_evaluado`, no en la nota. Test.
5. **El panel muestra el avance.** La vista Reglas lista las reglas activas con su recuento
   de productos marcados y su cobertura; la vista Hallazgos distingue detector de regla. Se
   ve con datos demo sin servidor.

---

## 11. Riesgos propios de S1c-1

- **Escala del cruce regla × producto.** N reglas × M productos por store view. En
  Renovapadel son ~decenas de reglas × 3.681 productos evaluables — trivial. A 228.881
  productos y cientos de reglas hay que agrupar por scope y recorrer el espejo una vez por
  set, como hace el perfilador. El diseño carga por set; la medición a escala es un test
  declarado, no un supuesto.
- **La dedup por causa raíz puede fundir causas distintas** si dos reglas legítimamente
  distintas comparten atributo en el mismo producto. Mitigación: la clave de causa es
  `(atributo)` sólo para carencias de campo; rango/formato/grupo mantienen su `code`. El
  test de sabotaje fija ambos lados.
- **Sin reglas aceptadas, el motor no aporta.** Es correcto por diseño (nada no-curado
  puntúa), pero significa que el valor de S1c-1 se ve recién cuando hay reglas aceptadas.
  Por eso la vista Reglas muestra también las `borrador` disponibles para curar (en
  lectura), para que el operador vea qué hay para aceptar.
- **Datos demo vs reales.** El panel se valida con demo hasta el desbloqueo del servidor;
  la forma real de los datos del cliente puede diferir. Es el mismo riesgo declarado en
  S0/S1a y se cierra con la ingesta real.
