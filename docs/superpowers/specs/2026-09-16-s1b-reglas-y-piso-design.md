# S1b — Reglas, piso externo y curación: diseño

**Creado:** 2026-09-16 · **Depende de:**
`2026-09-11-s1-perfilador-design.md` (que a su vez depende del spec maestro
`2026-09-08-standardskudo-catalog-quality-design.md`, revisión 3). Aquéllos son la
autoridad; este documento resuelve lo que el diseño de S1 deja abierto para **S1b** y no
los contradice en ningún punto.
**Estado:** en diseño. Aprobado en brainstorming el 2026-09-16. Sin código.

---

## 1. Qué decide este documento

El diseño de S1 (§7) define S1b como *"inferencia de reglas desde el perfil con confianza,
evidencia y excepciones candidatas; lectura del piso externo desde el mapeo de Google;
mapa de conceptos; almacenamiento y API de curación —aceptar, ajustar, rechazar por lote—
**sin interfaz gráfica**"*. La forma de la tabla `rule` ya está fijada en §5 de ese
documento y no se rediscute aquí.

Lo que queda abierto, y sin lo cual no se puede escribir un plan:

1. **Cómo se infiere cada `kind` de regla** desde las tablas del perfil, y con qué umbral.
2. **Cómo se representa el piso externo de Google** cuando sus requisitos por categoría no
   existen como archivo machine-readable.
3. **Qué es una transición de curación válida** y cómo se registra su historial.
4. **Cómo se puebla el mapa de conceptos** en su primera versión.

Las tres decisiones que el brainstorming cerró, y que gobiernan lo demás:

- **El mapa de conceptos se construye completo en S1b**, no como esqueleto: tabla, CRUD por
  CLI y población inicial desde el perfil.
- **El piso externo se cura por tipo de producto de Google**, no por las 641 categorías del
  tenant ni por scraping: se codifican los ~30 grupos de la taxonomía de Google que cubren
  el catálogo, más los universales.
- **La obligatoriedad se infiere con dos umbrales** (`>=0.90` confianza alta, `0.70–0.89`
  media con marca de ambigüedad, `<0.70` no se infiere). Los cortes son constantes del
  código, calibrables contra el catálogo real como los del perfilador.

---

## 2. El hecho de referencia que gobierna el diseño

Medición de cómo lo resuelven las herramientas de referencia (norma del proyecto):

- **Akeneo DQI** separa tres capas que nunca colapsa: *completitud* (atributos que un
  humano marcó obligatorios por familia × canal), *enriquecimiento* (todos los atributos
  con valor) y *consistencia* (formato, ortografía). Los atributos obligatorios en Akeneo
  **siempre los configura un humano, nunca se infieren**; su nuevo agente propone un
  modelo de datos pero un humano lo acepta antes de que gobierne la nota.
- **Google Merchant Center** usa tres niveles: universalmente requerido (`id`, `title`,
  `description`, `link`, `image_link`, `price`, `availability`), *condicionalmente*
  requerido por categoría (indumentaria exige `size`, `color`, `gender`, `age_group`), y
  recomendado. Las condiciones están **codificadas por `google_product_category`**, no se
  infieren. La taxonomía es un archivo plano de ~5.800 categorías; los requisitos **no**
  son un archivo aparte, viven en las páginas de la especificación.
- **Semrush Site Audit** corre 140+ checks fijos. Sin inferencia, sin personalización por
  sitio.

De ahí las tres formas que S1b adopta, cada una de una herramienta distinta:

> **Tres orígenes que nunca se colapsan** (§5 del spec): `piso_externo` (lo exige el canal,
> como el nivel condicional de Google), `inferida` (lo dice el catálogo, como la propuesta
> del agente de Akeneo), `curada` (lo escribió una persona). El primero no se rechaza, el
> segundo nace en borrador y no puntúa, el tercero es autoridad humana directa.

Y el patrón a **evitar**, también de Akeneo: contar como carencia todo atributo sin valor
(su "enriquecimiento"). Con 109 atributos de media por set en este tenant, eso son millones
de hallazgos falsos. La cobertura por partición del perfilador es la respuesta correcta, y
S1b infiere reglas SOBRE esa cobertura, no sobre la pertenencia al set.

---

## 3. La inferencia de reglas

El motor lee las tablas del perfil (`profile_partition`, `profile_attribute_coverage`,
`profile_value_stats`) de una `ProfileRun` sellada y **terminada**, y escribe reglas en
estado `borrador`. Nunca lee el espejo directamente: el perfil es su única entrada, y eso
lo hace reproducible —dos inferencias sobre el mismo perfil dan las mismas reglas— y
barato de re-correr.

### 3.1 Obligatoriedad — la regla que más volumen produce

Para cada `(partición, atributo)` de una `AttributeCoverage`:

```
sea c = coverage           # presente / (presente + vacio), o None
si c is None:              → no se infiere (no hay denominador)
si presente + vacio < MIN_EVIDENCIA:  → no se infiere (muestra chica)
si c >= UMBRAL_ALTO (0.90):
    regla obligatoriedad, confidence = c, sin marca
elif c >= UMBRAL_MEDIO (0.70):
    regla obligatoriedad, confidence = c, definition.ambiguo = true
else:                      → no se infiere
```

- **`confidence` es la cobertura misma**, no un número escrito a mano: el spec exige que se
  derive de la evidencia. La banda (`>=0.90` vs `0.70–0.89`) sólo decide si la regla lleva
  la marca `ambiguo`, que el curador ve y que hace que la regla no pueda aceptarse por lote
  ciego (§5.2).
- **`evidence_count` = presente**, los productos que la sostienen.
- **`scope`** es el de la partición: si la partición tiene subtipo (`splitter_value` no
  nulo), `scope_kind = subtype`; si es homogénea, `scope_kind = attribute_set`; la
  partición de set desconocido no produce reglas (su cobertura es `None`).
- **`store_view_magento_id`** es el de la `ProfileRun`. Una regla inferida SIEMPRE nace con
  store view: el perfil es por store view y un atributo al 90 % en PY y al 12 % en BR son
  dos hechos distintos (§6 del diseño de S1). Consolidar dos reglas iguales de dos store
  views en una regla `store_view = NULL` es una acción de curación, no de inferencia.

`MIN_EVIDENCIA`, `UMBRAL_ALTO`, `UMBRAL_MEDIO` son constantes de
`src/skudo/rules/inference.py` con su justificación al lado, **no configuración por
tenant** —misma lección de la válvula del barrido de S0 y de los umbrales del perfilador—.
Los valores de arriba son una hipótesis; se calibran en la última tarea del plan contra el
catálogo real y quedan escritos en `docs/superpowers/s1b-calibracion.md`.

### 3.2 Las excepciones candidatas

Toda regla inferida guarda, en su columna `exceptions`, los productos de la partición que
**no la cumplen** —para obligatoriedad, los que tienen el atributo `vacio`—: hasta
`MAX_EXCEPCIONES` SKUs con su evidencia, y un flag `truncado` si hay más. Es lo que hace
cumplible el criterio de aceptación 3 del spec: el curador inspecciona una regla y ve
ejemplos reales de **lo que marcaría** (las excepciones) y, por contraste con la cobertura,
de lo que descartaría.

Las excepciones se calculan en la inferencia y se congelan con la regla: son sobre el mismo
perfil que la generó, así que un producto que aparece como excepción es reproducible.

### 3.3 Rango — atributos numéricos

Para cada `ValueStats` con `kind = numerico`, `n_present >= MIN_EVIDENCIA` y
`n_ambiguous / n_present < MAX_RATIO_AMBIGUO (0.10)`:

- regla `rango` con `definition = {"attribute": code, "min": p05, "max": p95}`.
- `confidence` = `1 - (n_ambiguous / n_present)`: cuanto más limpio el atributo, más se
  confía en su rango.

El rango es `[p05, p95]` y no `[min, max]` a propósito: los extremos absolutos son
justamente los valores sospechosos que el detector de plausibilidad de S1c tiene que poder
marcar. Una regla que abarcara `[min, max]` no marcaría nunca nada.

Los atributos con `n_ambiguous / n_present >= 0.10` **no producen regla de rango** y en su
lugar generan una regla `kind = formato` con `definition.sospecha_conversion = true`: son el
insumo del detector de sospecha de conversión de S1c, no un rango en el que se pueda confiar.

### 3.4 Los `kind` que S1b NO infiere todavía

`plantilla_nombre`, `unidad` y `filtrable` se **modelan** (la columna `kind` los admite y
`definition` tiene su forma documentada) pero **no se infieren en S1b**. La plantilla de
nombre necesita el poder discriminante cruzado con la posición en el texto, la unidad
necesita el mapa de conceptos poblado, y `filtrable` necesita la sonda (S5). Se emiten como
reglas `curada` cuando alguien las escribe a mano por CLI, y la inferencia automática es
trabajo de S1c/S5. Modelarlos ahora sin inferirlos evita una migración después y deja la
puerta abierta —YAGNI con la puerta abierta, como la profundidad de partición del
perfilador—.

---

## 4. El piso externo de Google

### 4.1 La tabla de requisitos

`google_floor` es una tabla **de referencia, no por tenant**: los requisitos de Google
Shopping son los mismos para todos los clientes. Su forma:

| Campo | Qué es |
|---|---|
| `id` | PK |
| `category_group` | grupo de la taxonomía de Google, ej. `"Apparel & Accessories"`, o `"*"` para universal |
| `google_category_min` / `google_category_max` | rango de ids de Google que el grupo cubre; `NULL/NULL` para el universal |
| `google_attribute` | el atributo del canal, ej. `"color"`, `"size"`, `"gtin"` |
| `requirement` | `required` · `recommended` |
| `axis` | el eje del spec al que pertenece el hallazgo que produce |
| `applicability` | JSONB con la condición de aplicabilidad, ej. `{"solo_si": "fabricante_asigna_gtin"}` |
| `note` | por qué, en una línea |

Se puebla con un **seed curado** (`src/skudo/rules/floor_seed.py` o un JSON versionado):
los ~7 universales, más los requisitos condicionales de los grupos de la taxonomía de
Google que cubren las 641 categorías mapeadas del tenant —del orden de 30 grupos:
indumentaria, calzado, electrónica, deportes, hogar, etc.—. Curar 30 grupos, no 641
categorías: los requisitos de Google son por grupo grande, y un rango
`[min, max]` de ids cubre cada grupo entero.

El seed se cura mirando la especificación de datos de producto de Google. **No se scrapea**:
la especificación cambia, el scraping es frágil, y 30 grupos son curables a mano una vez y
revisables cuando Google cambie. Cada fila del seed lleva su `note` con la razón.

### 4.2 Cómo se convierte en reglas

Para cada tenant, el motor de piso:

1. Lee, del espejo, el `google_category_id_int` de cada categoría del tenant (está en los
   atributos, lo puso `Standard_GoogleCategory`).
2. Para cada categoría **mapeada**, busca en `google_floor` los grupos cuyo rango
   `[min, max]` contiene ese id, más los universales.
3. Emite una regla `origin = piso_externo` por `(atributo de Google, requisito)`, con:
   - `scope_kind = category`, `scope_key` = la categoría de Magento.
   - `status = aceptada` de nacimiento —el piso no se cura, ya es autoridad—.
   - `definition` que **mapea el atributo de Google al atributo del espejo vía el mapa de
     conceptos** (§5). Si el mapa no tiene equivalencia para ese atributo de Google, la
     regla nace con `definition.sin_mapeo = true` y `status = aviso`: no puede puntuar
     contra un atributo que no sabe leer, y eso se declara en vez de adivinar.

Las **635 categorías sin mapear** no producen reglas de piso: sus productos quedan con esos
controles en `desconocido`, que es la verdad. Generan en cambio un hallazgo del eje 11
(*"categoría sin mapeo externo"*) —pero ese hallazgo lo **emite S1c**, no S1b; S1b sólo
deja la ausencia registrada—.

### 4.3 El piso no se rechaza, sólo se acota

Una regla `piso_externo` no admite la transición a `rechazada` (§6). Lo que admite es
**acotar su aplicabilidad**: pasar a `aviso` para una categoría donde el requisito no
aplica de verdad (el caso del GTIN que el fabricante no asigna), registrando el motivo. Esto
es lo que el spec llama "acotar por aplicabilidad" y es lo que impide que el sistema aprenda
el error sistemático del catálogo como si fuera la norma.

---

## 5. El mapa de conceptos

### 5.1 La tabla

`concept_map` es **por tenant** (los nombres de atributo son del tenant):

| Campo | Qué es |
|---|---|
| `id` | PK |
| `tenant_id` | FK |
| `canonical` | el concepto canónico, ej. `"color"`, `"talle"`, `"peso"`, `"gtin"` |
| `attribute_code` | el atributo del espejo que lo implementa |
| `relation` | `equivalente` · `sinonimo` · `unidad_de` |
| `confidence` | 0–1 |
| `origin` | `inferida` · `curada` |

La clave natural es `(tenant_id, canonical, attribute_code)`: un concepto puede mapear a
varios atributos (`color` y `colour` ambos son `color`) y un atributo puede servir a varios
conceptos, así que ni `canonical` ni `attribute_code` son únicos por sí solos.

### 5.2 Población inicial

Dos fuentes, en orden de confianza:

1. **Seed de universales de Google** (`origin = curada`, `confidence = 1.0`): el mapeo
   fijo `title→name`, `description→description`, `image_link→image`, `price→price`,
   `availability→status/visibility`, `gtin→` (el atributo que el tenant use). Es lo que el
   piso externo necesita para leer el espejo, y es curado porque es una decisión, no una
   medición.
2. **Sinónimos inferidos del perfil** (`origin = inferida`): se comparan los nombres de
   atributo del tenant buscando pares que sean el mismo concepto —`color`/`colour`,
   `talle`/`size`/`tamano`, `peso`/`weight`, `marca`/`brand`—. La confianza sale de la
   similitud de nombre normalizado **y** de que las coberturas no se solapen (dos atributos
   sinónimos rara vez están ambos llenos en el mismo producto). Nacen para revisión: un
   sinónimo inferido con `confidence < 1.0` es un candidato que el curador confirma, no un
   hecho.

La inferencia de sinónimos es deliberadamente conservadora: un falso sinónimo
(`precio`/`precio_especial` mapeados al mismo concepto) haría que dos atributos distintos se
traten como uno, que es peor que no tener el sinónimo. Ante la duda, no se infiere.

---

## 6. Vida y muerte de una regla — las transiciones

El estado vive en `rule.status` y toda transición se registra en `rule_version`. Los
estados son los de §5 del diseño de S1: `borrador`, `aceptada`, `aviso`, `rechazada`.

Las transiciones **permitidas**, según origen:

| Desde → hasta | `inferida` | `piso_externo` | `curada` |
|---|:-:|:-:|:-:|
| borrador → aceptada | ✓ | — (nace aceptada) | ✓ |
| borrador → rechazada | ✓ | — | ✓ |
| aceptada → aviso | ✓ (auto por FP o manual) | ✓ (acotar) | ✓ |
| aceptada → rechazada | ✓ | ✗ **prohibida** | ✓ |
| aviso → aceptada | ✓ | ✓ | ✓ |
| cualquiera → rechazada | ✓ | ✗ **prohibida** | ✓ |

Dos reglas que el modelo hace cumplir, no la disciplina de quien cura:

- **`piso_externo` nunca llega a `rechazada`.** La función de transición rechaza esa
  combinación con un error, no con un log. Es el candado de §5 del spec.
- **La degradación por falso positivo es automática.** Cuando S1c mida
  `false_positive_rate` y supere `UMBRAL_FP`, la regla pasa sola de `aceptada` a `aviso` y
  se escribe un `rule_version` con `actor = "sistema"`, `motivo = "fp > umbral"`. No depende
  de que alguien se acuerde. (El disparo lo hace S1c, que es quien mide; S1b provee la
  transición y la registra.)

### 6.1 `rule_version` — el historial

| Campo | Qué es |
|---|---|
| `id` | PK |
| `rule_id` | FK |
| `from_status` / `to_status` | la transición; `from_status` NULL en el alta |
| `actor` | quién: email del curador, o `"sistema"`, o `"inferencia"` |
| `motivo` | texto libre; obligatorio en `rechazada` y en acotar el piso |
| `definition_snapshot` | JSONB: la `definition` **en el momento** de la transición, para que un ajuste sea auditable |
| `created_at` | cuándo |

### 6.2 `ruleset_snapshot` — la versión con la que se puntúa

S1c necesita puntuar contra un conjunto de reglas **congelado y nombrado**, para que dos
scorings del mismo catálogo con las mismas reglas den lo mismo y para que un cambio de
reglas sea un evento datable. `ruleset_snapshot` guarda, por tenant y store view, la lista
de ids de regla `aceptada`/`aviso` vigentes en un instante, con una versión incremental.
S1b lo **crea** por CLI; S1c lo **consume**. La columna `ruleset_version` de `rule` (§5 del
spec) apunta al snapshot con el que la regla se puntuó por última vez.

---

## 7. La API de curación — por CLI, sin UI

Toda la curación de S1b es por línea de comandos sobre la lógica de
`src/skudo/rules/curation.py`. La UI es S1d y no arranca hasta que haya reglas reales que
curar (§7 del diseño de S1). El diseño de la capa de curación se piensa **como si una
pantalla la fuera a consumir**: funciones puras que reciben ids y devuelven resultados
estructurados, para que S1d sea una vista sobre ellas y no una reimplementación.

Comandos (todos `skudo rules …`, sobre el patrón del `cli.py` existente):

| Comando | Qué hace |
|---|---|
| `infer --tenant T --store S` | corre la inferencia sobre el último `ProfileRun` de esa store view; escribe reglas `borrador`. Idempotente: re-inferir sobre el mismo perfil no duplica. |
| `floor --tenant T` | genera/actualiza las reglas de piso externo desde `google_floor` y el espejo. |
| `list --tenant T [--status …] [--kind …] [--origin …] [--store S]` | lista reglas con su confianza, evidencia y estado. |
| `inspect RULE_ID` | muestra la regla, su definición, y sus **excepciones candidatas** con SKUs reales: qué marcaría y, por cobertura, qué descartaría. |
| `accept RULE_ID [RULE_ID …]` | acepta por lote. **Rechaza aceptar a ciegas una regla `ambiguo`**: exige `--confirm-ambiguo` para las de banda media, para que el lote no cuele una regla dudosa. |
| `reject RULE_ID --reason "…"` | rechaza con motivo. Falla sobre `piso_externo`. |
| `limit RULE_ID --category C --reason "…"` | acota un piso externo a `aviso` para una categoría. Sólo sobre `piso_externo`. |
| `adjust RULE_ID --definition '{…}'` | ajusta la definición; registra el snapshot anterior en `rule_version`. |
| `snapshot --tenant T --store S` | congela las reglas activas en un `ruleset_snapshot` nuevo. |
| `concepts …` | subcomandos del mapa de conceptos: `infer`, `list`, `add`, `remove`, `confirm`. |

El token, la contraseña y cualquier secreto **nunca** viajan por la línea de comandos:
S1b no los necesita —trabaja sobre el espejo y el perfil, ya en Postgres—, así que la regla
de S0 se mantiene por construcción.

---

## 8. Estructura de archivos

```
src/skudo/rules/
  __init__.py
  models.py       rule, rule_version, ruleset_snapshot, google_floor, concept_map
  inference.py    perfil → reglas borrador; umbrales como constantes
  floor.py        google_floor + espejo → reglas piso_externo
  floor_seed.py   el seed curado de requisitos de Google (datos, no lógica)
  concepts.py     mapa de conceptos: seed + inferencia de sinónimos
  curation.py     transiciones puras: accept/reject/limit/adjust/snapshot
  transitions.py  la máquina de estados: qué transición es válida por origen
tests/rules/
  test_models.py
  test_inference.py
  test_floor.py
  test_concepts.py
  test_curation.py
  test_transitions.py
alembic/versions/
  0019_rules_and_floor.py   las cinco tablas nuevas
```

Reglas de frontera:

- `inference.py` **sólo lee el perfil**. No importa nada del espejo ni de `findings`. Si
  necesita un dato que el perfil no tiene, el dato falta en el perfil y es un cambio de
  S1a, no un atajo en S1b.
- `curation.py` es puro sobre la sesión: recibe ids y strings, devuelve objetos. Ninguna de
  sus funciones imprime ni lee de `argv`. El CLI las envuelve. Es lo que deja a S1d
  construirse encima sin reescribir la lógica.
- `transitions.py` es la **única** autoridad sobre qué transición es válida. Ni el CLI ni
  `curation.py` deciden por su cuenta que un piso no se rechaza: preguntan.

---

## 9. Lo que S1b NO hace

- **No evalúa.** No produce ni un `Finding`. Reglas + espejo → hallazgos es S1c, y la
  separación es del spec (§5.7): el motor de reglas es una función pura y las reglas son su
  entrada, no su producto.
- **No mide falsos positivos.** La columna `false_positive_rate` nace `NULL` y la llena S1c
  contra la muestra etiquetada. S1b sólo provee la transición que la degradación dispara.
- **No llama IA.** Toda la inferencia es determinista y explicable.
- **No infiere plantilla de nombre, unidad ni filtrable.** Se modelan, no se infieren
  (§3.4).
- **No tiene UI.** La curación es por CLI (§7).

---

## 10. Criterios de aceptación

Los tres del spec para S1b, cada uno con su prueba:

1. **Ninguna regla inferida puntúa antes de ser aceptada.** Prueba: una regla `inferida`
   nace `borrador`; `transitions` prohíbe que una `borrador` entre a un `ruleset_snapshot`;
   sólo `accept` la mueve a `aceptada`. Test de sabotaje: intentar snapshotear un borrador
   falla.
2. **Una regla del piso externo no se puede rechazar, sólo acotar.** Prueba: `reject` sobre
   una `piso_externo` levanta error; `limit` la pasa a `aviso` con motivo. Test de
   sabotaje: recorrer todos los orígenes y afirmar que sólo `piso_externo` rechaza el
   `reject`.
3. **Las excepciones candidatas se inspeccionan con ejemplos reales de lo que marcaría y de
   lo que descartaría.** Prueba: `inspect` de una regla de obligatoriedad devuelve los SKUs
   `vacio` (lo que marcaría) y el conteo `presente` (lo que descartaría), ambos del perfil
   que la generó.

Y dos invariantes propias, medidas como el perfilador mide las suyas:

4. **Reproducibilidad:** dos `infer` sobre el mismo `ProfileRun` producen el mismo conjunto
   de reglas (misma definición, misma confianza, mismas excepciones).
5. **Ninguna regla sin evidencia:** toda regla `inferida` tiene `evidence_count > 0` y
   `confidence` que coincide con la cobertura de su partición. Un test recorre las reglas
   generadas contra el perfil y lo verifica.

---

## 11. Riesgos propios de S1b

- **El seed del piso de Google envejece.** Google cambia su especificación. Mitigación: el
  seed es datos versionados con `note` por fila, no lógica; revisarlo es un diff legible, y
  un cambio de Google es una edición del seed, no del motor.
- **La inferencia de sinónimos es el punto más frágil.** Un falso sinónimo contamina el
  mapa de conceptos y de ahí el piso externo. Mitigación: conservadurismo (§5.2), `origin`
  visible, y confianza < 1.0 que exige confirmación humana antes de que gobierne.
- **Los umbrales son hipótesis hasta la calibración.** `UMBRAL_ALTO`, `UMBRAL_MEDIO`,
  `MIN_EVIDENCIA` son valores de arranque. La última tarea del plan los calibra contra el
  catálogo real y deja la medición escrita, igual que la Task 9 del perfilador.
- **La calibración depende de datos reales en el espejo**, que hoy están bloqueados por la
  red del servidor ([[skudo-servidor-produccion]]). El plan puede construir y probar todo
  S1b contra fixtures; la tarea de calibración queda declarada y se corre cuando la ingesta
  real esté disponible, como pasa con la Task 9 de S1a.
