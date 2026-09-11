# S1 — Perfilador, reglas y puntuación: diseño

**Creado:** 2026-09-11 · **Depende de:** `2026-09-08-standardskudo-catalog-quality-design.md`
(revisión 3), que es la autoridad. Este documento resuelve lo que aquel deja abierto para
S1 y no lo contradice en ningún punto.
**Estado:** en diseño. Sin código.

---

## 1. Qué decide este documento

El spec define S1 como *"perfilador con reglas por subtipo, confianza, evidencia y
excepciones"*. Tres cosas quedan sin definir ahí, y sin ellas no se puede escribir un
plan:

1. **Qué es un subtipo.** El spec lo usa dieciocho veces y nunca dice cómo se obtiene.
2. **Cómo se representa una regla** y qué la hace aceptable, rechazable o degradable.
3. **Dónde termina S1.** Tal como está escrito incluye una UI de curación, y hoy no
   existe ni una línea de la aplicación web.

Y una cuarta, que la medición del catálogo real vuelve urgente: **qué significa "vacío"**
cuando cada attribute set declara entre 92 y 209 atributos.

---

## 2. El hecho que gobierna el diseño de S1

Medición sobre el catálogo del tenant piloto:

| | |
|---|---|
| Atributos definidos | 1.066 |
| De opción (`select` + `multiselect`) | 935 |
| **Marcados obligatorios en Magento** | **13** |
| Atributos por attribute set | **92 mínimo, 109 de media, 209 máximo** |
| Presentes en casi todos los sets | 82 |

Un producto pertenece a un set que declara **109 atributos de media**, y Magento sólo
exige trece en todo el catálogo. La inmensa mayoría de los atributos de un producto están
vacíos, **y eso es normal**: un set agrupa una familia entera, no un modelo.

De ahí la regla que gobierna todo S1:

> **Pertenecer al attribute set no es evidencia de obligatoriedad.** Una regla que exija
> un atributo porque el set lo declara produciría del orden de veinte millones de
> hallazgos falsos en este tenant. La obligatoriedad se **infiere de lo que el catálogo
> hace**, no de lo que el set permite.

Es la formulación operativa del principio rector del spec —confundir un dato incorrecto
con uno desconocido o con una excepción válida es el peor fallo posible— aplicada al eje
con más volumen.

---

## 3. Qué es un subtipo

**Un subtipo es una partición del attribute set que cambia materialmente qué atributos se
llenan.** No es un concepto del catálogo ni una etiqueta que alguien escriba: es un
objeto **descubierto y medido**, con evidencia y con un criterio de aceptación.

### 3.1 El procedimiento

Para cada `(attribute set, store view)` con al menos `MIN_PARTICION` productos:

1. Se calcula el **vector de cobertura** del set: para cada atributo del set, qué
   proporción de sus productos lo tiene presente.
2. Se prueban **candidatos a divisor**: la categoría comercial asignada, y cada atributo
   de opción del propio set con cardinalidad efectiva baja (2 a `MAX_CARD` valores).
3. Para cada candidato se calculan los vectores de cobertura de cada grupo resultante.
4. Se acepta el divisor que **más reduce la ambigüedad** del conjunto, si la reduce por
   encima de `MIN_GANANCIA` y si **cada grupo** conserva al menos `MIN_PARTICION`
   productos.

La **ambigüedad** de un vector de cobertura es la media de `min(c, 1−c)` sobre sus
atributos. Un atributo que está en el 100 % o en el 0 % de los productos no es ambiguo:
dice algo. Uno que está en el 50 % no dice nada — y es exactamente la situación en la que
una regla de obligatoriedad marcaría mal a la mitad del grupo. Dividir bien es convertir
un 50/50 en dos grupos de 95/5, y ahí sí hay regla.

La partición es de **profundidad 1** en la primera versión: un solo divisor por set. La
estructura de datos admite profundidad mayor y el perfilador no la usa hasta que haya
evidencia de que hace falta. YAGNI con la puerta abierta.

### 3.2 Por qué así y no de otra forma

Se consideraron tres alternativas:

- **Subtipo = attribute set.** Es lo que el spec descarta explícitamente, y la medición
  explica por qué: con 92 a 209 atributos por set, el set es demasiado grueso.
- **Subtipo = categoría hoja.** Tentador, pero la categoría es navegación y merchandising:
  un mismo modelo vive en "Ofertas" y en "Notebooks". Sirve como **candidato a divisor**,
  no como definición.
- **Subtipo curado a mano.** 298 sets en uso × 5 tenants. Es la configuración manual que
  la decisión de arquitectura del spec ya descartó.

El descubrimiento medido tiene además una propiedad que ninguna de las tres tiene: **dice
cuándo no hay subtipo**. Si ningún divisor gana, el set es homogéneo y la regla se emite a
nivel de set, con esa decisión registrada y justificada.

### 3.3 Los umbrales, y por qué son constantes y no configuración

`MIN_PARTICION`, `MAX_CARD` y `MIN_GANANCIA` se fijan en el código con su justificación al
lado y **no se exponen como configuración por tenant**. Un umbral configurable termina
bajando hasta que todo pasa —es la misma lección de la válvula del barrido de S0—, y aquí
bajarlo produce reglas que marcan defectos donde hay variedad legítima. Se cambian
editando el código, con el cambio de valor visible en el diff y en la revisión.

Los valores iniciales se calibran contra el catálogo real en la primera tarea del plan y
quedan escritos con la medición que los justifica.

---

## 4. Los cuatro estados, resueltos contra el espejo

El spec exige cuatro estados que nunca se colapsan: **presente, vacío, no aplica,
desconocido**. El espejo permite derivarlos sin ambigüedad, y conviene dejar escrito cómo,
porque la derivación ingenua se equivoca:

| Situación en el espejo | Estado |
|---|---|
| La clave está en `product_record.attributes` con valor no vacío | **presente** |
| La clave está con `""`, o no está, y el atributo pertenece al set del producto | **vacío** |
| El atributo **no** pertenece al `attribute_set_id` del producto | **no aplica** |
| `product_record.attribute_set_id` es `NULL` | **desconocido** (todos los atributos) |
| El atributo no está en el espejo de atributos | **desconocido** |

Dos trampas que esta tabla evita:

- **`"0"` es un valor presente.** Un stock de cero, un peso de cero declarado a propósito,
  una opción cuyo id es 0. Evaluar la verdad de la cadena en vez de su presencia convierte
  datos legítimos en carencias. La misma trampa que el espejo ya documenta en S0.
- **Ausencia de clave ≠ no aplica.** El módulo devuelve lo que existe en las tablas EAV;
  un atributo del set sin fila es un **vacío**, y ésa es justamente la carencia que el
  producto tiene que reportar. Confundirlo con "no aplica" apagaría el eje 3 entero.

`desconocido` **nunca cuenta como cobertura ni como carencia**: entra en el denominador de
la cobertura declarada del spec (6.4) y en ningún otro sitio.

---

## 5. La regla: forma, vida y muerte

Una regla es una fila, no código. Vive en `rule` — el spec la llama `rules`; el esquema
construido en S0 usa nombres de tabla en singular (`product_record`, `attribute`,
`category`) y S1 sigue esa convención. Su forma:

| Campo | Qué es |
|---|---|
| `scope_kind` | `global` · `attribute_set` · `subtype` · `category` |
| `scope_key` | a qué partición se aplica |
| `store_view_magento_id` | `NULL` si vale para todas |
| `axis` | el eje del spec al que pertenece el hallazgo que produce |
| `kind` | `obligatoriedad` · `rango` · `formato` · `plantilla_nombre` · `unidad` · `filtrable` |
| `definition` | JSONB, según `kind` |
| `confidence` | 0–1, derivada de la evidencia, no escrita a mano |
| `evidence_count` | productos que la sostienen |
| `exceptions` | excepciones candidatas, con su motivo |
| `status` | `borrador` · `aceptada` · `aviso` · `rechazada` |
| `false_positive_rate` | medida contra muestra etiquetada; `NULL` mientras no se mida |
| `ruleset_version` | la versión con la que se puntuó |
| `origin` | `inferida` · `piso_externo` · `curada` |

Tres propiedades que el spec exige y que la forma hace cumplibles:

- **Una regla inferida nace en `borrador` y no puntúa.** Sólo `aceptada` puntúa como
  defecto; `aviso` produce hallazgo sin penalizar. Nada que nadie haya mirado puede bajar
  la nota de un catálogo.
- **El piso externo no se infiere y no se puede rechazar por curación**, sólo acotar por
  aplicabilidad. Es lo que impide que el sistema aprenda el error sistemático como norma.
- **Una regla con `false_positive_rate` por encima del umbral se degrada sola a `aviso`.**
  La degradación es automática y queda registrada: el spec lo exige y no depende de que
  alguien se acuerde.

### 5.1 El piso externo, con lo que ya existe

`Standard_GoogleCategory` mapea 641 de 1.276 categorías a la taxonomía de Google Shopping
en el atributo de categoría `google_category_id_int`. El piso externo se **lee** de ahí:
para cada categoría mapeada, los requisitos del canal para esa categoría de Google se
convierten en reglas de `origin = piso_externo` sobre los productos que cuelgan de ella.

Las 635 categorías sin mapear producen un hallazgo del eje 11 y **no producen reglas**:
sus productos quedan con esos controles en `desconocido`, que es la verdad, y la cobertura
declarada lo muestra.

---

## 6. El perfil es por store view, y eso no es un detalle

Cobertura, distribuciones y poder discriminante se calculan **por `(attribute set, store
view)`**. Un atributo puede estar al 90 % en PY y al 12 % en BR: es exactamente el hallazgo
que el producto existe para encontrar, y promediarlo lo borra.

La consecuencia práctica: las particiones descubiertas pueden diferir entre store views, y
eso es información, no incoherencia. El perfil guarda ambas.

---

## 7. Descomposición de S1

El S1 del spec es demasiado grande para un solo plan, y contiene una UI para una aplicación
que todavía no existe. Se parte en cuatro, cada uno con software que funciona y se prueba
por sí mismo:

### S1a — Perfilador

Descubrimiento de particiones, cobertura por los cuatro estados, distribuciones de valores
numéricos, cardinalidad efectiva y poder discriminante, todo por `(partición, store view)`
y reproducible desde una generación del espejo. Tablas de perfil, comando `profile` en la
CLI, informe legible.

*Aceptación:* dos pasadas sobre el mismo espejo producen perfiles idénticos; el perfil
declara para cada set si encontró subtipo y con qué divisor; los umbrales están calibrados
contra el catálogo real con la medición escrita al lado; un atributo con cobertura muy
distinta entre PY y BR aparece como tal y no promediado.

### S1b — Reglas y piso externo

Inferencia de reglas desde el perfil con confianza, evidencia y excepciones candidatas.
Lectura del piso externo desde el mapeo de Google. Mapa de conceptos. Almacenamiento y API
de curación —aceptar, ajustar, rechazar por lote— **sin interfaz gráfica**: lo que existe
es el modelo, sus transiciones y su historial.

*Aceptación:* ninguna regla inferida puntúa antes de ser aceptada; una regla del piso
externo no se puede rechazar, sólo acotar; las excepciones candidatas de una regla se
pueden inspeccionar con ejemplos reales de lo que marcaría **y de lo que descartaría**.

### S1c — Evaluación y puntuación

Motor determinista puro: reglas + espejo → hallazgos. Los detectores que no necesitan IA
ni sonda: filter-blind, filtro perdido, filtro inútil, plantilla de nombre, plausibilidad
física, campos basura, unidades, candidatos a duplicado, categorías sin mapeo externo,
sets muertos. Cuatro estados, cobertura, errores críticos, deduplicación por causa raíz,
PHS y grado. Arnés de medición de falsos positivos contra muestra etiquetada.

*Aceptación:* la del spec para S1, con el criterio de curación medido en cobertura de
catálogo (45 % en una sesión, 80 % en la primera semana) y diez candidatos a filtro
perdido aceptados por el equipo de catálogo.

### S1d — UI de curación

Donde empieza la aplicación web. Fuera del alcance de S1a–c y planificable sólo cuando
haya reglas reales que curar; hasta entonces la curación se hace por CLI sobre la API de
S1b, que es suficiente para el piloto y no bloquea nada.

**Orden:** S1a → S1b → S1c, y S1d cuando S1b produzca reglas que merezcan una pantalla.

---

## 8. Lo que S1 no hace

- **No escribe en Magento.** Eso es S3, y el orden del spec —precisión antes que
  escritura— es deliberado.
- **No llama a la IA.** Eso es S6. Todo detector de S1c es determinista y explicable.
- **No lee la página publicada.** Los ejes 9, 10 y 12 necesitan la sonda: son S5.
- **No usa demanda de búsqueda interna**, que en este tenant no existe. Los detectores
  que dependen de ella se declaran no computables, no se aproximan con un sustituto peor.

---

## 9. Riesgos propios de S1

| Riesgo | Mitigación |
|---|---|
| **Inferir obligatoriedad de la pertenencia al set** (109 atributos de media) | Obligatoriedad medida sobre el subtipo descubierto, con umbral de ganancia; nada se exige por estar declarado |
| Umbrales que se relajan hasta que todo pasa | Constantes en código con su justificación, no configuración por tenant |
| Una partición minoritaria legítima marcada como defecto | `MIN_PARTICION` por grupo; excepciones candidatas explícitas; falsos positivos medidos por regla |
| Reglas en borrador que puntúan sin que nadie las mire | Sólo `aceptada` penaliza; `borrador` no aparece en el score |
| Perfilar promediando store views y borrar el hallazgo | El perfil es por `(partición, store view)` en el esquema, no por convención |
| Perfil no reproducible que hace indiscutible cualquier debate sobre una regla | Perfil sellado con la generación del espejo; dos pasadas iguales dan bytes iguales |
| Medir el avance de la curación en número de sets | El criterio es cobertura de catálogo; hay 124 sets vacíos que lo premiarían |
