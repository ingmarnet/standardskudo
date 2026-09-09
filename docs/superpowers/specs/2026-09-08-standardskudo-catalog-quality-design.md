# StandardSkudo — Plataforma de Calidad de Catálogo

**Producto:** StandardSkudo (`Standard` + `SKU` + *escudo*; el juego funciona igual en
español y portugués). Miembro de la familia `Standard_*`, junto a
`Standard_SemanticSearch`.
**Creado:** 2026-09-08 · **Revisión 2:** 2026-09-09 (revisión técnica externa incorporada)
**Estado:** en diseño. Sin código.

---

## 1. Resumen ejecutivo

StandardSkudo es un SaaS multitenant que evalúa y corrige la calidad del registro de
productos en tiendas Magento, a nivel de *store view*.

El sistema se instala como un módulo en el Magento del cliente, mantiene un espejo
canónico del catálogo, observa además la superficie publicada (páginas renderizadas y
búsquedas reales), infiere las reglas de calidad a partir del propio catálogo, puntúa
cada registro, prioriza los hallazgos por impacto comercial y propone lotes de
corrección que un humano aprueba antes de que se escriban.

**Tenant piloto:** Nissei — cientos de miles de SKUs, dos store views (Paraguay y
Brasil), ~100 attribute sets, ritmo de cambio constante.
**Objetivo comercial:** 5 tenants en el primer año.

**El principio que gobierna todo el diseño:** el mayor riesgo del sistema no es dejar
de detectar un defecto, es **confundir un dato incorrecto con un dato desconocido o con
una excepción válida**. Una corrección equivocada aplicada en masa destruye más valor
que la ausencia de la herramienta. De ahí la exigencia de evidencia por dato, la
cobertura visible en la puntuación y la aprobación humana obligatoria.

---

## 2. El problema

El catálogo se registra mediante un sistema propio de carga por lotes que a veces
produce registros de baja calidad ("registro rápido"): nombres que son códigos
internos, categorías equivocadas, atributos vacíos, pesos y dimensiones con errores de
escala, datos guardados en el campo equivocado, productos sin imagen.

Dos consecuencias de diseño:

1. **Es un problema de flujo, no de stock.** Hay un proceso que produce defectos de
   forma continua. Corregir solo aguas abajo es una cinta de correr. De ahí la medición
   de calidad en el origen y la validación pre-registro.
2. **El registrador también reescribe productos existentes.** Puede pisar las
   correcciones aplicadas, y puede modificar un campo entre el momento en que
   proponemos una corrección y el momento en que la escribimos. La detección de
   regresión y el control de concurrencia son obligatorios.

El coste no es estético. Un atributo filtrable vacío es un producto que el cliente no
puede encontrar. Un peso mal cargado es un flete mal cotizado. Son pérdidas
cuantificables y el producto debe expresarlas así.

---

## 3. Estado del mercado y posicionamiento

Investigación del 2026-09-08. No existe ningún producto que cubra la intersección
buscada. Cinco categorías, cada una con un hueco estructural:

| Categoría | Ejemplos | Hueco |
|---|---|---|
| PIM con data quality | Akeneo (Data Quality Insights, grados A–E), Salsify, Sales Layer, inriver | El PIM quiere *ser* el master: exige migrar el catálogo. No audita Magento in situ |
| Digital Shelf Analytics | Profitero, DataWeave, Syndigo Content Health, Stackline, NIQ | Auditan tus productos en retailers *ajenos*. Óptica de marca/CPG. Sin write-back |
| SEO | Semrush, Amasty SEO Toolkit, Mageplaza SEO Reports, AuditIQ | SEO a nivel página, no semántica a nivel atributo ni por tipo de producto |
| GEO / AEO | Scrunch, AthenaHQ, Nudge, Pumice | Miden citación de marca, no calidad de registro |
| Bulk edit Magento | Amasty Mass Product Actions, Meetanshi, Magefan | Músculo de ejecución sin criterio: no detecta ni prioriza |

**Arte previo más relevante:** `magendooro/magento2-catalog-quality` (open source).
Product Health Score 0–100, grados A–E, ejes Enrichment + Consistency, detector
*filter-blind*. Módulo mono-tienda, sin SEO, sin IA, sin multitenancy, sin remediación.
**Su modelo de scoring debe leerse antes de implementar S1.**

### Nuestra posición defendible

Se apoya en dos cosas que se pueden **demostrar**, no argumentar:

1. **Coherencia verificable del registro.** Que el nombre, los atributos, el texto, la
   media y los datos estructurados no se contradigan entre sí, y que cada afirmación
   tenga evidencia interna trazable. Un registro incoherente es defectuoso para
   humanos, para buscadores y para cualquier canal, sin necesidad de afirmar nada sobre
   cómo funciona un modelo de lenguaje.
2. **Encontrabilidad medida, no inferida.** En lugar de deducir que un atributo vacío
   esconde un producto, se ejecutan búsquedas representativas y se comprueba si el
   producto aparece. Esto convierte la tesis del producto en una medición.

A eso se suman cuatro capacidades que ninguna herramienta investigada tiene:

3. Audita el catálogo donde vive, a scope de store view, sin migrar nada.
4. Reglas inferidas del catálogo, por tipo de producto, con curación humana.
5. Remediación masiva con evidencia, aprobación, control de concurrencia,
   verificación independiente y rollback.
6. **Medición de calidad en el origen del registro** y **detección de regresión**.

**Nota sobre GEO.** La documentación de Google es explícita: no hay optimizaciones
especiales para AI Overviews ni AI Mode —ni schema propio, ni `llms.txt`, ni markup
para IA, ni algoritmo de ranking separado— y las buenas prácticas de SEO siguen siendo
las relevantes
([AI Features and Your Website](https://developers.google.com/search/docs/appearance/ai-features)).
Por lo tanto el producto **no afirma mecanismos de citación en modelos de lenguaje**.
Lo que el eje 10 evalúa —accesibilidad, desambiguación, coherencia y cobertura de
preguntas— se justifica por sí solo, sin apelar a cómo lee un LLM.

---

## 4. Decisiones tomadas

| Decisión | Elección | Razón |
|---|---|---|
| Audiencia | SaaS multitenant desde el inicio | Consecuencia: no se puede contar con acceso a la base de datos del cliente |
| Conector | Módulo Magento instalable + API | Lectura masiva eficiente, deltas, write-back en bloque. La REST estándar no aguanta 400k registros |
| Persistencia | Espejo canónico en Postgres | Re-scoring barato al iterar reglas, base para rollback, perfilado estadístico, historia |
| Observación externa | Sonda de superficie publicada, por muestreo | Hay hallazgos que el espejo no puede ver por definición (ver 5.4) |
| Origen de las reglas | Inferidas + curación humana + piso externo obligatorio | 100 attribute sets × 5 tenants hacen imposible la configuración manual |
| Alcance GEO | Controles medibles sobre el registro, sin afirmar mecanismo | Ver nota en 3 |
| Remediación | Aprobación humana obligatoria, con concurrencia controlada | El primer catálogo destrozado por un bug mata el producto |
| Priorización | Ventas + búsqueda interna + GA4 + stock/margen, con incertidumbre visible | Sin peso comercial, 40.000 hallazgos son una lista inservible |
| UI | App SaaS propia (Next.js) | Coherente con multitenancy |
| Stack | Python + Postgres + Next.js; módulo en PHP | La parte difícil es procesamiento de datos e IA a escala |
| Aislamiento de cómputo | Cola y worker por tenant | Con 5 tenants grandes, el pipeline compartido no aporta nada |

---

## 5. Arquitectura

### 5.1 Componentes

```
  Magento del tenant                    Nuestra infraestructura
 ┌────────────────────┐
 │ Módulo PHP         │   deltas   ┌──────────────┐
 │                    │ ─────────► │ Ingestor     │
 │ · lectura masiva   │            │ (por tenant) │
 │ · cola de cambios  │            └──────┬───────┘
 │ · señales comerc.  │                   ▼
 │ · escritura x lote │ ◄────────  ┌──────────────────────┐
 │ · validación pre-  │  write-back│ ESPEJO CANÓNICO      │
 │   registro         │            │ Postgres             │
 └────────────────────┘            └──────┬───────────────┘
        ▲                                 │
        │ páginas + búsquedas             │
 ┌──────┴───────────┐                     │
 │ SONDA de         │─── observaciones ───┤
 │ superficie       │                     │
 │ publicada        │                     │
 │ (por muestreo)   │                     │
 └──────────────────┘                     │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                   ┌────────────┐  ┌────────────┐  ┌────────────┐
                   │ Perfilador │  │ Motor de   │  │ Capa IA    │
                   │ (infiere   │─►│ reglas     │◄─│ (selectiva)│
                   │  reglas)   │  │ (puro)     │  └────────────┘
                   └────────────┘  └─────┬──────┘
                                         ▼
                                 ┌───────────────┐   ┌──────────────┐
                                 │ Scorer        │──►│ API + App    │
                                 │ PHS+cobertura │   │ Next.js      │
                                 │ +impacto      │   └──────┬───────┘
                                 └───────────────┘          │
                                 ┌───────────────┐          │
                                 │ Remediación   │◄─────────┘
                                 │ evidencia/    │  aprobación humana
                                 │ concurrencia/ │
                                 │ verif./RB     │
                                 └───────────────┘
```

### 5.2 El ciclo cerrado de verificación

Cuando se aprueba un lote y el módulo escribe, el cambio **vuelve como delta**. El
sistema no confía en su propia escritura.

Con una corrección importante respecto a la primera versión de este diseño: **la mejora
del score no demuestra que el cambio se aplicó.** La verificación exige una **lectura
independiente** que compruebe el valor exacto y el scope exacto de cada campo escrito.
Un score que sube puede subir por otra razón; el razonamiento circular no es
verificación.

### 5.3 El módulo Magento

No contiene lógica de calidad. Solo mueve datos:

- **Lectura masiva** paginada por cursor, con proyección de campos.
- **Cola de cambios** con watermark, para servir deltas.
- **Señales comerciales** agregadas: ventas por SKU y store view, búsquedas internas y
  sus resultados, disponibilidad vendible, margen.
- **Escritura por lote idempotente**, con clave de idempotencia por ítem, comprobación
  de valor esperado (ver 7.3) y respuesta detallada por ítem —aplicado / rechazado /
  conflicto / sin cambio— en lugar de un OK global.
- **Lectura de verificación** independiente del canal de escritura.
- **Validación pre-registro**: expone el motor de reglas como portero.

### 5.4 La sonda de superficie publicada

Componente separado del espejo, porque hay hallazgos que el espejo **no puede ver por
definición**: el catálogo contiene los datos, no lo que el cliente recibe.

Observa dos superficies:

- **Páginas renderizadas** (eje 9): respuesta HTTP, canonical efectivo, hreflang
  recíproco, indexabilidad, datos estructurados realmente emitidos, y el resultado
  visual del HTML de la descripción.
- **Búsquedas reales** (eje 12): consultas representativas por SKU, MPN, nombre,
  sinónimo local y necesidad de compra, comprobando si aparecen los productos esperados
  y si los filtros conservan los resultados que deberían conservar.

**Restricción de diseño:** no se puede renderizar 200k páginas × 2 store views de forma
rutinaria. La sonda trabaja por **muestreo estratificado y dirigido por impacto**:
productos de alto valor comercial, representantes por attribute set y categoría, y
todo lo que haya cambiado o se haya corregido recientemente. Su cobertura es siempre
parcial y **la puntuación debe declararlo** (ver 6.4).

Un hallazgo de la sonda debe clasificarse por **origen**: producto, plantilla,
configuración de la tienda, o índice. Un canonical mal emitido en 40.000 páginas casi
nunca es un problema de 40.000 productos.

### 5.5 El objeto central

Todo gira alrededor del **registro evaluable**: `(tenant, producto, store_view)`.

Un producto no tiene un score: tiene dos, uno en PY y otro en BR. Su nombre en
portugués puede estar mal aunque en español esté perfecto.

### 5.6 Modelo de datos

Regla de oro: **el espejo, las señales y las observaciones de la sonda son la única
fuente de verdad; todo lo demás se recalcula.**

**Espejo** (reflejo de Magento; se sobrescribe, nunca se edita a mano)
- `product_records` — una fila por `(tenant, sku, store_view)`. Atributos efectivos en
  JSONB **más la procedencia de scope** de cada valor (global / website / store view).
  Guardar la procedencia es imprescindible: corregir en el scope equivocado es el error
  de write-back más común en Magento.
- **Identidad desglosada**, no un campo único: `sku` interno, `mpn`, `modelo`,
  `gtin`, e identidad de variante. Se preservan **ceros iniciales y sufijos del
  fabricante** como texto; normalizarlos numéricamente destruye la identidad.
- `product_categories` — **la asignación es global**: en el core de Magento,
  `catalog_category_product` no tiene `store_id`. Se guarda por separado el **efecto
  por store view**, que se deriva de la root category del store group, del `is_active`
  de la categoría en esa tienda y de los websites asignados al producto. Confundir
  asignación con efecto fue un error de la primera versión de este diseño.
- `categories` — árbol, nombres por store view, flags de anchor, y **clase de
  categoría**: técnica, comercial o de campaña (ver eje 2).
- `attribute_sets`, `attributes` — código, tipo, scope, `is_filterable`, requerido.
- `attribute_options` — **`option_id` con sus labels por store view**. `Negro` y
  `Preto` pueden ser la misma opción traducida: la consolidación se decide por
  `option_id`, nunca por igualdad de etiqueta.
- `media_assets` — roles, dimensiones, peso, hash perceptual, alt, orden de galería,
  visibilidad efectiva por tienda, y resultado de la comprobación de carga.
- `product_relations` — configurables, hijos, atributos de variación.

**Señales** (ritmo distinto; opcionales por tenant)
- `signal_sales`, `signal_search`, `signal_analytics`, `signal_inventory` — agregados
  por SKU y store view. El inventario usa **disponibilidad vendible por canal**
  (descontando reservas y pedidos pendientes), no la cantidad física.

**Observaciones de la sonda**
- `page_observations` — por URL y store view: HTTP, canonical, hreflang,
  indexabilidad, schema emitido, timestamp, y qué muestra la produjo.
- `search_probes` — consulta, store view, resultados esperados, resultados obtenidos,
  comportamiento de los filtros.

**Reglas** (versionadas)
- `rules` — tenant, attribute set o subtipo, eje, definición JSONB, confianza,
  evidencia (% de catálogo que la respalda), estado, origen (inferida / piso externo /
  manual), **excepciones explícitas**, y **tasa de falsos positivos medida**.
- `rule_versions`, `ruleset_snapshots`.
- `concept_map` — atributos equivalentes, sinónimos, unidades y su uso por tipo. Base
  del eje 11 y de la interpretación de las búsquedas sin resultado.

**Hallazgos y scores** (derivado y desechable)
- `findings` — eje, regla, severidad, evidencia JSONB, estado, y **scope del
  hallazgo**: `product` | `attribute_set` | `attribute` | `category` | `template` |
  `store`. Un solo hallazgo de configuración puede explicar miles de hallazgos de
  producto: la UI debe mostrar la causa, no repetir el síntoma.
- **`finding_state`** con cuatro valores para el dato evaluado: `incorrecto`,
  `desconocido`, `no_aplica`, `contradictorio`. Nunca colapsarlos en "mal".
- `check_coverage` — por registro y control: `evaluado` / `no_evaluado` / `no_aplica`,
  con el motivo. Un control no evaluado **no cuenta como aprobado**.
- `scores` — PHS, grado, sub-scores por eje, **cobertura**, errores críticos, impacto,
  versión de ruleset.

**Evidencia** (transversal, ver 6.6)
- `evidence` — por dato y por corrección: fuente, fragmento o fila exacta, fecha,
  identidad exacta del producto de donde salió, y transformación aplicada. Los
  conflictos entre fuentes se guardan como conflicto, no se resuelven en silencio.

**Remediación** (auditoría; inmutable)
- `remediation_batches`, `remediation_items` (tipo de operación, campo, scope,
  **valor observado en el momento de proponer**, valor propuesto, confianza, esfuerzo,
  estado), `write_results`, `snapshots`, `regression_events`,
  `registration_cohorts`.

### 5.7 Fronteras entre componentes

- El **motor de reglas es una función pura**: registro + reglas → hallazgos. No
  consulta la base, no llama a Magento, no llama a la IA. Trivial de testear con casos
  fabricados, que es lo que hace falta con cientos de reglas cambiando cada semana.
- La **capa IA** se aísla detrás de `analizar(registro, tipo) → hallazgos` con caché
  por hash de contenido delante.
- El **perfilador** solo lee el espejo y escribe reglas en borrador.
- El **scorer** solo lee hallazgos, cobertura y señales.
- La **sonda** solo escribe observaciones; no puntúa ni decide.

---

## 6. El motor de calidad

### 6.1 Los doce ejes

Cada hallazgo pertenece a un eje, tiene severidad, un estado de los cuatro de 5.6, y
puede no aplicar según el tipo de producto.

**Eje 1 — Identidad y nomenclatura**
- Identidad desglosada y no confundida: `sku` interno, `mpn`, modelo, identidad de
  variante y `gtin` son cosas distintas y se validan por separado
- **Ceros iniciales y sufijos del fabricante preservados**: `0074`≠`74`, y
  `ABC-123/B` no es `ABC-123`
- El nombre no sigue la plantilla de su tipo (ver 6.2)
- El nombre *es* un código, no un nombre (`INV-WIFI89283942`)
- Basura en el nombre: código interno o SKU embebido, `NUEVO!!!`, MAYÚSCULAS, HTML,
  dobles espacios, caracteres de control
- Nombre sin traducir para la store view
- Marca ausente o inconsistente (`Samsung` / `SAMSUNG` / `Sansung`)
- **Duplicados como candidatos, nunca como veredicto.** La similitud de nombres genera
  un candidato; confirmar equivalencia exige revisar capacidad, color, revisión,
  región y presentación. Dos registros con nombre casi idéntico pueden ser dos
  productos legítimamente distintos
- **GTIN por aplicabilidad, no por ausencia.** Hay productos sin GTIN asignado y los
  canales lo contemplan; la regla exige GTIN donde corresponde y acepta la declaración
  de inexistencia donde no. Formato y dígito de control se validan solo si hay valor
- Error de tipología: debería ser variante y está suelto; configurable sin hijos;
  variantes que no comparten atributos de variación

**Eje 2 — Categorización**
- **Clase de categoría explícita**: técnica (taxonomía del catálogo), comercial
  (navegación y merchandising) y de campaña (temporal). Las reglas se aplican por clase,
  con **excepciones explícitas y documentadas**: un mouse pad en Mouses puede ser
  merchandising deliberado, no un error
- **Accesibilidad efectiva**: que el producto sea alcanzable desde el árbol
  correspondiente en esa tienda, no solo que exista una fila de asignación
- Asignación (global) frente a efecto (por tienda): un producto asignado puede no ser
  accesible en BR por root category, `is_active` o websites
- Sin categoría, solo en la raíz, o solo en una categoría cajón de sastre
- Categoría demasiado genérica existiendo una hoja adecuada
- Categoría incoherente con el producto (centrifugador en Secarropas) — IA, como
  candidato a revisión
- Sobre-categorización que diluye la navegación
- Salud del árbol: categorías vacías, duplicadas, profundidad excesiva

**Eje 3 — Atributos**
- **Cuatro estados, nunca colapsados**: vacío, desconocido, no aplica, contradictorio.
  "Vacío" no implica "incorrecto", y "no aplica" no es una carencia
- **Obligatoriedad por subtipo, no por attribute set.** Que un atributo esté completo en
  el 95% del set no demuestra que el 5% restante esté mal: puede ser un subtipo con otra
  ficha. La regla se emite al nivel más específico que la evidencia sostenga
- **Filter-blind**: atributo filtrable vacío ⇒ el producto no aparece al filtrar. El
  hallazgo con traducción más directa a dinero perdido
- **Sin poder discriminante**: el atributo tiene prácticamente el mismo valor en todo su
  tipo, así que no informa; ensucia el nombre y genera un filtro de una sola opción.
  Métrica inversa del filter-blind
- Valores basura: `N/A`, `-`, `SIN DATO`, `.`, `xx`. **`0` no es basura por sí mismo**:
  puede ser un valor válido y su tratamiento depende del atributo
- Unidades ausentes o mezcladas dentro del mismo atributo
- **Consolidación de opciones por `option_id`**, no por etiqueta: `Negro` y `Preto`
  pueden ser la misma opción traducida por store view. Comparar labels sin comparar
  identidad produce correcciones destructivas
- Atributos poblados que no aplican al tipo (ruido en filtros)
- Attribute set equivocado para el producto — IA, como candidato
- Contradicción atributo ↔ nombre
- Atributos traducibles sin traducir

**Eje 4 — Plausibilidad física**

Un peso mal cargado no es un defecto de calidad: es un flete mal cotizado.

- **Magnitudes separadas y no intercambiables**: peso neto, peso embalado, dimensiones
  del producto, dimensiones del paquete, unidad de cada una y **cantidad de bultos**.
  Mezclarlas es la causa más común de un cálculo de envío absurdo
- **Sospecha de conversión, no corrección automática.** Una diferencia de ×10 o ×1000
  contra la distribución del subtipo señala una **posible** conversión de unidad
  (gramos por kilos, milímetros por centímetros). Es un candidato de alta prioridad, y
  **la corrección exige evidencia adicional** —ficha del fabricante, otro campo
  coherente, un producto hermano— no solo la limpieza del factor.
  Ejemplo correcto: `400 × 203 × 302 mm` equivale a `40 × 20,3 × 30,2 cm`. El factor
  debe verificarse por dimensión y ser consistente en las tres; una tolerancia de
  redondeo no es lo mismo que un factor limpio
- **Chequeo de densidad**: peso contra volumen. Atrapa los dos campos a la vez y
  funciona sin conocer el tipo de producto
- **Peso volumétrico** calculado con **el divisor y la configuración del transportista
  que corresponda**, no con una constante global
- Dimensiones ausentes en productos que se envían; valores cero o negativos

**Eje 5 — Descripción larga**
- **Matriz de información decisiva por tipo**: uso, prestaciones, compatibilidad,
  limitaciones y contenido del paquete cuando corresponda. Se mide **cobertura y
  exactitud**, no longitud: un texto largo que no cubre la matriz puntúa peor que uno
  corto que la cubre
- **Cada afirmación técnica vinculada a evidencia interna.** Una prestación afirmada en
  el texto que ningún atributo respalda es un hallazgo, no un adorno
- Ausente o insuficiente para informar
- **Saneado de HTML conservando estructuras válidas de Page Builder**, y comprobación
  del **resultado visual** tras el saneado. Limpiar HTML a ciegas puede romper una
  maquetación legítima
- Texto duplicado en cientos de SKUs
- Descripción de otro producto por copy-paste — IA
- Contradice los atributos o el nombre — IA
- Idioma incorrecto para la store view
- Precios, promociones o plazos escritos en el texto: caducan y el producto miente
- Códigos de referencia que pertenecen a un atributo o al short
- Keyword stuffing, legibilidad

**Eje 6 — Descripción corta**
- **Formato configurable por tenant y por lugar donde aparece** (grid, comparador,
  feeds): no hay un único formato correcto y el sistema no debe imponer uno
- Ausente, o copia literal de la larga
- Detección de **beneficios repetidos**, **frases vacías** y **afirmaciones sin
  respaldo** en atributos
- Longitud fuera del rango útil del lugar donde se muestra

> **Pendiente (pregunta abierta 10).** El criterio editorial concreto de descripción
> corta para el tenant piloto —estructura de la lista, un tipo de información por
> bullet, uso de `<strong>`— **no está confirmado por el cliente** y por eso no se
> codifica aquí. Se implementa como plantilla configurable vacía hasta que el criterio
> se confirme de primera mano.

**Eje 7 — Media**
- **Comprobación de existencia real**: que el archivo cargue, que sea una imagen
  válida y que **corresponda al producto o variante exactos**. Una entrada en la galería
  no demuestra nada
- Sin imagen, o solo un placeholder
- **Hash compartido ⇒ revisión, no defecto.** Hay reutilización legítima (variantes,
  packs, familias); el hallazgo es un candidato a revisar
- Menos imágenes de las que su tipo necesita; **roles exigidos por aplicabilidad**
- Resolución insuficiente, proporciones inconsistentes, peso excesivo
- Alt text ausente o igual al nombre del archivo
- **Orden de la galería**, fotos que se contradicen entre sí, y **visibilidad efectiva
  por tienda**
- Variantes de color sin foto propia
- Marca de agua o texto promocional quemado en la imagen

**Eje 8 — Coherencia comercial**
- **Estados de negocio explícitos**: borrador, preventa, agotado temporal, en
  reposición, descatalogado. Visibilidad y stock se evalúan **contra la política de ese
  estado**, no contra una regla única. Un preventa sin stock y visible es correcto
- **Disponibilidad vendible por canal**, considerando reservas y pedidos pendientes,
  además de la cantidad física
- Precio 0 o ausente; precio especial caducado
- Comparación de precios **con contexto de moneda, impuestos y promociones** entre PY y
  BR: una diferencia bruta no es una incoherencia
- Visibilidad, estado y websites incoherentes entre store views

**Eje 9 — SEO por store view** *(requiere la sonda)*
- **Lectura de la página publicada y renderizada**: respuesta HTTP, canonical efectivo,
  hreflang recíproco, indexabilidad y **datos estructurados realmente emitidos**. El
  espejo del catálogo no permite comprobar nada de esto
- **Clasificación por origen**: producto, plantilla, configuración o índice. Determina
  quién puede arreglarlo y si la corrección es una o cuarenta mil
- Meta title y description ausentes o duplicados. **Las longitudes son orientaciones,
  no reglas**: se reportan como aviso, no como defecto
- URL key ausente, con basura, duplicada, o cambiada sin redirect
- Contenido duplicado entre store views
- El término por el que la gente busca ese producto no aparece en el nombre ni en el
  title (cruce con búsqueda interna)

**Eje 10 — Controles de accesibilidad y coherencia del contenido**

Antes llamado *GEO-readiness*. Se reformula como controles medibles con justificación
independiente, sin afirmar mecanismos de citación (ver nota en 3).

- **Accesibilidad**: los atributos decisivos están expresados en el texto y no solo en
  una tabla, y el contenido es alcanzable sin depender de la interpretación de un
  layout. Justificación: sirve a lectores humanos, a feeds y a extracción automática
- **Desambiguación**: el nombre y la identidad permiten distinguir este producto de sus
  hermanos sin ambigüedad
- **Coherencia**: nombre, atributos, descripción y datos estructurados emitidos no se
  contradicen entre sí. Es el control con mayor valor demostrable del eje, y se verifica
  contra la salida real de la sonda
- **Cobertura de preguntas de compra**: las preguntas frecuentes de ese tipo de producto
  tienen respuesta en la ficha. La lista de preguntas se deriva de la búsqueda interna y
  se cura, no se inventa

**Eje 11 — Diseño del catálogo** *(configuración, no productos)*

Un producto no puede tener bien un atributo que el attribute set no define.

- **Mapa de conceptos** como base del eje: atributos equivalentes, sinónimos, unidades y
  uso por tipo. Sin él, las conclusiones sobre atributos faltantes son ruido
- **Atributos sugeridos para el tipo**: atributos que ese tipo debería tener y el set no
  define. Fuentes: piso externo por categoría; **búsqueda interna**; especificaciones
  presentes en el texto del tipo sin existir como atributo; y benchmark anonimizado
  entre tenants solo con adhesión explícita (ver pregunta 9)
- **Una búsqueda sin atributo correspondiente no siempre revela un atributo faltante**:
  puede revelar un sinónimo del mapa de conceptos, o una relación de compatibilidad. Las
  tres lecturas se distinguen antes de proponer nada
- **Filtro perdido**: atributo con buen dato, buena cobertura y demanda que no está
  marcado como filtrable
- **Filtro inútil**: filtrable con un solo valor efectivo en esa categoría
- **La propuesta es por categoría; la escritura no puede serlo.** En Magento estándar
  `is_filterable` es propiedad del **atributo** (`catalog_eav_attribute`) y se aplica a
  toda la tienda; la navegación muestra los filtrables de los sets presentes en cada
  categoría, pero no se configura por categoría sin una extensión. El análisis se emite
  por `(categoría, atributo, store view)` para que sea útil; la operación de escritura
  declara su alcance real —el atributo completo— y advierte del efecto colateral en las
  demás categorías. Este era un error de la primera versión de este diseño
- Orden y posición de los filtros según demanda de búsqueda
- Salud de los attribute sets: solapados, redundantes, "Genérico" sobrecargado

**Eje 12 — Encontrabilidad efectiva** *(requiere la sonda)*

Cierra el círculo de la tesis del producto: en lugar de inferir que un registro pobre
esconde el producto, se mide.

- **Búsquedas representativas** por SKU, MPN, nombre, sinónimo local y necesidad de
  compra, por store view
- ¿Aparecen los productos esperados? ¿En qué posición?
- ¿Los filtros **conservan** los resultados que deberían conservar? Un filtro que
  descarta productos correctos es tan dañino como uno ausente
- **Detecta el caso que ningún otro eje ve**: productos con ficha completa y buen PHS
  que siguen siendo difíciles de encontrar. Ese hallazgo apunta a configuración de
  búsqueda, sinónimos o mapa de conceptos, no a calidad de registro
- El tenant piloto ya opera `Standard_SemanticSearch`, así que la sonda de búsqueda se
  apoya en infraestructura existente

**Eje transversal — Proceso** *(evalúa cómo se registra, no el producto)*
- **Edad del registro y periodo de gracia.** Un producto registrado hace tres horas en
  modo rápido está incompleto por diseño: es **backlog de enriquecimiento**, no **deuda
  de calidad**. Dos colas distintas para dos personas distintas
- **Regresión**: campos corregidos que volvieron a degradarse. Obligatorio
- **Cohortes de registro**: agrupación por proximidad temporal y firma de defectos
- **Calidad en el origen**: si el registrador sella lote, usuario y modo, atribución por
  proceso y por persona
- **Velocidad**: ¿el backlog se drena o crece? Es el único número que le dice a
  dirección si el problema se está resolviendo

### 6.2 Reglas: inferencia, curación, piso externo

El perfilador analiza el espejo y propone reglas en borrador, **al nivel más específico
que la evidencia sostenga** (subtipo antes que attribute set):

- Cobertura de atributos por subtipo, con excepciones candidatas explícitas
- Distribuciones de valores numéricos por subtipo: base de la sospecha de conversión y
  de los rangos plausibles
- Poder discriminante de cada atributo
- **Plantilla de nombre por tipo**: qué atributos aparecen en los nombres bien formados
  y en qué orden. `Aire Acondicionado Inverter Wifi 18.000 BTU` no es una frase, es
  `tipo + tecnología + conectividad + capacidad`. La plantilla incluye solo atributos con
  poder discriminante — así nunca propone meter "negro" en el nombre de una cámara
  profesional, pero sí en el de una funda de celular
- Formatos y unidades canónicas por atributo
- Mapa de conceptos: sinónimos y equivalencias candidatas

Cada regla lleva **confianza**, **evidencia** (% de catálogo que la respalda),
**excepciones** y **tasa de falsos positivos medida** contra una muestra etiquetada.

**El piso externo** es obligatorio y no se infiere: los campos exigidos por Google
Shopping, Meta catalog y GS1, **con sus condiciones de aplicabilidad** (un producto sin
GTIN asignado es un caso previsto, no un defecto). Impide que el sistema aprenda un
error sistemático como norma.

**La curación** es una UI donde el operador acepta, ajusta o rechaza reglas por lote,
viendo ejemplos reales de lo que cada regla marcaría **y de lo que descartaría**.

### 6.3 Capa IA y control de coste

Principio: **nunca llamar a la IA para algo que una regla puede decidir.**

1. Reglas deterministas sobre el 100% del catálogo
2. **Triage**: la IA solo sobre lo que las reglas marcan como sospechoso o sobre SKUs de
   alto impacto
3. **Caché por hash de contenido**: si el contenido relevante no cambió, no se vuelve a
   pagar
4. **Modelo por tarea**: económico para clasificación, capaz solo para generación
5. **Presupuesto por tenant y mes**, con parada dura visible en la UI

La IA produce **candidatos con evidencia**, nunca hechos. Toda salida de IA entra al
sistema como hallazgo o propuesta sujeta a aprobación.

**Extracción de atributos desde el texto existente** es la capacidad más rentable y a
menudo no necesita IA: `Notebook Lenovo 8GB 15.6"` ya contiene la RAM y las pulgadas.
El dato no falta, está mal guardado; estructurarlo no inventa nada. Orden de
preferencia: parser específico → IA de extracción → nada.

**Advertencia que gobierna la extracción:** extraer correctamente un valor de una
descripción equivocada produce un dato equivocado con apariencia de evidencia. Por eso
toda extracción registra de qué fragmento y de qué identidad de producto salió (6.6), y
un conflicto entre fuentes se declara como conflicto.

### 6.4 Puntuación

Cuatro salidas, no una:

- **PHS 0–100 y grado A–E** por `(producto, store view)`
- **Cobertura**: qué porcentaje de los controles aplicables se pudo evaluar de verdad.
  Un PHS de 82 con cobertura del 40% no es comparable con un 82 al 95%, y presentarlos
  igual es engañoso. Un control no evaluado queda **pendiente**, nunca aprobado
- **Errores críticos**: defectos que impiden considerar el producto **listo para
  publicar**, independientemente del PHS. Un producto sin imagen o con peso implausible
  no es un A con matices
- **Sub-score por eje**, para decidir dónde invertir

Dos correcciones sobre la primera versión:

- **Sin doble penalización.** Una misma contradicción se manifiesta hoy en los ejes 1,
  3, 5 y 10 a la vez. Los hallazgos se **deduplican por causa raíz** antes de puntuar, y
  se penaliza una vez en el eje donde la causa vive
- **Falsos positivos medidos por regla** contra muestras etiquetadas, publicados junto a
  la regla. Una regla con falsos positivos altos se degrada a aviso automáticamente

Los pesos por eje se definen por tipo de producto. El grado siempre por store view.
Todo score guarda la versión de ruleset.

### 6.5 Priorización por impacto comercial

`impacto = severidad × efecto_en_descubribilidad × peso_comercial × confianza ÷ esfuerzo`

Con la incertidumbre **visible**, no escondida en un número:

- **Confianza** del hallazgo y de la corrección propuesta
- **Esfuerzo** estimado de corrección: mil productos con una corrección automática de
  alta confianza valen más que diez que exigen investigación manual
- **Demanda potencial**, no solo histórica. La fórmula original tenía un sesgo grave:
  un producto nuevo o poco visible no tiene ventas ni tráfico, así que quedaba siempre
  al final de la cola — exactamente el backlog de enriquecimiento que hay que drenar.
  La demanda potencial se estima por su categoría y subtipo, no por su historia
- **Medición posterior del resultado observado**, considerando estacionalidad y otros
  cambios comerciales. La prioridad es una **estimación**, y el sistema debe aprender de
  lo que pasó de verdad en vez de confiar en su propia fórmula

El stock y el estado de descatalogado filtran antes de gastar IA.

### 6.6 Evidencia por dato y por corrección

Control transversal, y probablemente el más importante del sistema.

Cada dato inferido o corregido guarda:

- **Fuente** — atributo, descripción, ficha del fabricante, canal, IA
- **Fragmento o fila exacta** de donde salió
- **Fecha** de la observación
- **Identidad exacta del producto** al que pertenecía esa fuente
- **Transformación aplicada** — conversión de unidad, parseo, normalización

Y cuando dos fuentes discrepan, **el conflicto se guarda como conflicto**. No se resuelve
por precedencia silenciosa: se muestra a quien aprueba.

Sin esto, una corrección correcta y una corrección plausible pero equivocada son
indistinguibles en la UI, y a escala de 400k registros esa indistinción es el riesgo
principal del producto.

---

## 7. Remediación

### 7.1 Cinco tipos de operación

1. **Rellenar** — poner un valor donde no hay ninguno
2. **Normalizar** — formato, unidad, capitalización, consolidación de opciones **por
   `option_id`**
3. **Reubicar** — el valor es correcto y está en el campo equivocado. Sacar
   `cf23489243-1/12` del nombre y ponerlo en el atributo de referencia. **Atómico**:
   quitarlo sin haberlo guardado antes destruye información
4. **Reconstruir** — regenerar el campo desde la plantilla del tipo más los atributos
   (nombres), o generarlo (descripción, short)
5. **Reconfigurar** — cambiar la estructura: crear un atributo que falta en un set,
   cambiar `is_filterable`, retirar un filtro inútil, reordenar filtros, mover productos
   a un set más adecuado

   **Alcance real declarado.** `is_filterable` es propiedad del atributo y afecta a
   toda la tienda: la propuesta puede originarse en una categoría, pero la operación
   debe declarar que el efecto es global sobre ese atributo, listar las demás categorías
   afectadas, y advertir del coste en el índice de navegación por capas. Limitar el
   efecto a categorías concretas requiere una extensión y **no se promete en el core**.
   Vía de aprobación separada, con aprobador administrador y despliegue por etapas.

### 7.2 Ciclo de un lote

```
propuesta (con evidencia y valor observado) → preview + diff → aprobación humana
   → revalidación de concurrencia → snapshot → escritura por lote
   → lectura independiente de verificación → resultado por ítem
                                                    │
                                             rollback condicional
```

- **Preview**: diff campo por campo, scope de cada escritura, evidencia de cada valor,
  muestra representativa, conteo afectado, impacto estimado y **confianza**
- **Aprobación humana obligatoria.** Nada se escribe sin un clic
- **Snapshot** del estado anterior de los campos afectados

### 7.3 Concurrencia: la corrección atada a lo que se revisó

Corrección de un fallo de la primera versión de este diseño. Sabemos que el registrador
por lotes reescribe productos existentes, así que entre proponer y escribir el campo
puede haber cambiado. Escribir entonces el valor aprobado es un *lost update*: se pisa
un dato más nuevo con una decisión tomada sobre un dato viejo.

- Cada `remediation_item` guarda el **valor observado en el momento de proponer**
- La aprobación queda **atada a ese valor**
- Antes de escribir, se revalida: si el campo cambió, **la propuesta se invalida** y
  vuelve a la cola como nueva propuesta sobre el estado actual. No se escribe "de todos
  modos"
- La escritura viaja con comprobación de valor esperado, y el módulo devuelve
  `conflicto` como resultado distinto de `rechazado`

### 7.4 Verificación independiente

**La mejora del score no demuestra que el cambio se aplicó.** La verificación lee, por
un camino independiente del de escritura, el **valor exacto y el scope exacto** de cada
campo escrito, y compara con lo aprobado. Solo eso cierra el lote.

### 7.5 Rollback condicional

El rollback tampoco puede ser ciego: entre la escritura y la vuelta atrás alguien pudo
hacer un cambio legítimo. Antes de revertir se comprueba que el valor actual sigue
siendo el que escribimos; si no, el rollback de ese ítem se detiene y se escala, en vez
de pisar una modificación posterior.

### 7.6 Detección de regresión

Obligatoria. El espejo tiene historia, así que el sistema detecta lo que ninguna
herramienta sin espejo puede: *"este campo se corrigió el 12 de marzo y volvió a quedar
vacío el 3 de abril"*. Eso no es un hallazgo nuevo, es una **alerta de que algo aguas
arriba pisa las correcciones**. Se agrupa por cohorte y por campo para señalar la causa,
no para volver a corregir de uno en uno.

---

## 8. Validación pre-registro (el portero)

Lo más rentable del sistema. Corregir 200.000 registros cuesta dinero cada vez; impedir
que entre uno malo cuesta una llamada de validación.

El sistema de registro por lotes es propio, así que el mismo motor de reglas puro se
expone como endpoint: recibe un lote antes de que entre y devuelve *"entraría con grado
D; faltan estos 4 atributos obligatorios del subtipo; el peso de estos 12 está fuera de
rango plausible"*.

Cero lógica nueva. Convierte el producto de limpiador en portero.

**Instrumentación recomendada del registrador** (pequeña, no bloqueante):
- **Sellar el origen**: lote, usuario y modo en cada producto creado
- **Marcar el modo rápido**: un flag que declare "esto entró incompleto a propósito".
  Sin él, la juventud se infiere por fecha de creación, que funciona pero confunde
  "nuevo e incompleto" con "viejo y roto" en los casos de borde

---

## 9. Multitenancy, seguridad y custodia

Custodiamos el catálogo de nuestros clientes: es el riesgo principal del negocio.

- **Aislamiento** por tenant a nivel de fila, verificado en la capa de acceso
- **Cola y worker por tenant**
- **Credenciales por tenant**, rotables, permisos mínimos, lectura separada de escritura
- **Cifrado** en reposo y en tránsito; secretos fuera del código
- **Borrado verificable** al cancelar; auditoría conservada anonimizada
- **Roles**: operador (ve y propone), aprobador (escribe), administrador (reglas,
  presupuesto y configuración), solo-lectura
- **Log de auditoría inmutable** de toda escritura, con quién aprobó qué y cuándo
- **Presupuesto de IA por tenant** con parada dura
- **La sonda respeta** `robots.txt`, límites de tasa y ventanas horarias del tenant
- Residencia de datos: PY y BR, a verificar por tenant

---

## 10. Explícitamente fuera de alcance

- **Compatibilidad y relaciones entre productos** (memoria compatible con una
  motherboard, cartucho para una impresora). Propuesto en la revisión externa y
  reconocido como valioso, pero exige adquirir datos de modelo exacto que no están en el
  catálogo: es un producto aparte, no un eje. Documentado como línea futura
- **Aptitud por canal de publicación** (Merchant Center y otros: comparar catálogo,
  feed, página y datos estructurados, e incorporar rechazos reales del canal).
  Igualmente valioso e igualmente futuro; convertiría el piso externo en validación
  operativa con requisitos versionados por canal, mercado y tipo
- Monitoreo de citaciones en LLMs
- Ser el master del dato: no somos un PIM
- Plataformas distintas de Magento
- Escritura automática sin aprobación humana
- Traducción masiva PY↔BR como producto
- Optimización de precios o gestión de stock

---

## 11. Descomposición en sub-proyectos

Orden derivado de la revisión externa: **primero precisión y evidencia, después
escritura segura, después medición externa.** Construir remediación sobre reglas
imprecisas es automatizar el error.

### S0 — Conector y espejo canónico
Módulo PHP (lectura por cursor, cola de cambios, deltas, señales, escritura idempotente
con valor esperado, lectura de verificación independiente). Ingestor por tenant. Espejo
con **procedencia de scope**, identidad desglosada, `option_id` con labels por tienda,
asignación de categoría global **más efecto por store view**, disponibilidad vendible.

*Aceptación:* espejo de 200k SKUs × 2 store views sincronizado; un cambio manual aparece
dentro del SLA de delta; procedencia de scope y efecto de categoría por tienda
verificados a mano en una muestra; una opción traducida PY/BR se reconoce como la misma
`option_id`.

### S1 — Perfilador, reglas y puntuación
Perfilador con reglas por subtipo, confianza, evidencia y excepciones. Piso externo con
aplicabilidad. Mapa de conceptos. UI de curación. Motor determinista puro. Los cuatro
estados del dato. Cobertura y errores críticos. Deduplicación por causa raíz. Medición
de falsos positivos con muestras etiquetadas.

Detectores deterministas: filter-blind, poder discriminante, plausibilidad física
(magnitudes separadas, sospecha de conversión, densidad, volumétrico por transportista),
plantilla de nombre, candidatos a duplicado, campos basura, unidades. Eje 11 en modo
solo detección.

*Aceptación:* catálogo puntuado con cobertura declarada; en muestra ciega los grados
coinciden con la percepción del equipo de Nissei; falsos positivos por regla medidos y
bajo umbral acordado; las reglas de los 10 attribute sets mayores se curan en una sesión
de trabajo; ningún candidato a duplicado se presenta como duplicado confirmado.

### S2 — Evidencia por dato
Trazabilidad completa (fuente, fragmento, fecha, identidad, transformación), conflictos
entre fuentes declarados, y la UI que los muestra a quien aprueba.

*Aceptación:* toda propuesta muestra de dónde salió cada valor; un conflicto fabricado
entre dos fuentes aparece como conflicto y no se resuelve solo.

### S3 — Remediación determinista con escritura segura
Priorización con confianza, esfuerzo y demanda potencial. Las cinco operaciones. Ciclo
de lote con concurrencia atada al valor observado, snapshot, escritura, **verificación
independiente**, rollback condicional y detección de regresión. Vía de configuración
separada para la operación 5, con alcance real declarado.

Entrega valor real **sin una sola llamada a IA**: sospechas de conversión confirmadas,
nombres reconstruidos desde atributos existentes, códigos reubicados, unidades y
opciones normalizadas.

*Aceptación:* lote aplicado y verificado por lectura independiente; una modificación
concurrente inducida a propósito **invalida** la propuesta en vez de pisarla; rollback
condicional se detiene ante un cambio posterior; una regresión inducida se detecta y se
atribuye.

### S4 — Portero pre-registro
El motor de reglas como validación de lotes antes de la creación. Reutiliza S1.
Adelantable si el sangrado por registro rápido es urgente.

*Aceptación:* un lote deliberadamente malo se rechaza o advierte con el detalle del por
qué, antes de crear nada.

### S5 — Sonda de superficie publicada: SEO y encontrabilidad
Sonda con muestreo estratificado. Eje 9 con página renderizada y clasificación por
origen. **Eje 12: encontrabilidad efectiva**, apoyada en `Standard_SemanticSearch`.
Eje 10 reformulado como controles medibles verificados contra la salida real.

*Aceptación:* se detecta al menos un caso real de producto con PHS alto que no aparece
en una búsqueda esperada; un problema de plantilla se clasifica como plantilla y no como
40.000 defectos de producto; la cobertura de la sonda se declara en la puntuación.

### S6 — Capa IA
Extracción desde texto, clasificación de categoría y set, detección de incoherencias,
generación de nombre, descripción y short. Caché, triage, presupuesto.

*Aceptación:* precisión medida contra conjunto etiquetado por el equipo de catálogo, con
umbral por tarea; coste de pasada completa en presupuesto; ninguna salida de IA llega al
catálogo sin aprobación; toda extracción trae su fragmento de origen.

### S7 — Multitenancy productiva
Aislamiento endurecido, onboarding autoservicio, roles, presupuestos, borrado
verificable, informes exportables.

*Aceptación:* un segundo tenant se incorpora sin intervención de ingeniería.

### Alcance del piloto

Las primeras reglas se concentran en **identidad, atributos, medidas físicas y media**:
son las que permiten demostrar correcciones concretas y verificables, con evidencia
clara y bajo riesgo de falso positivo.

---

## 12. Riesgos

| Riesgo | Mitigación |
|---|---|
| **Confundir dato incorrecto con dato desconocido o excepción válida** (riesgo principal) | Cuatro estados del dato; obligatoriedad por subtipo; excepciones explícitas; evidencia por dato; falsos positivos medidos; aprobación humana |
| Las reglas inferidas aprenden el error sistemático como norma | Piso externo con aplicabilidad; confianza y evidencia visibles; curación con ejemplos de lo que se descarta |
| Corrección plausible pero equivocada aplicada en masa | Evidencia por dato con fragmento e identidad de origen; conflictos declarados; despliegue por etapas |
| El registrador pisa correcciones, o cambia un campo entre propuesta y escritura | Concurrencia atada al valor observado; regresión obligatoria; portero pre-registro |
| Verificación circular (creer que se aplicó porque subió el score) | Lectura independiente de valor y scope exactos |
| Rollback que pisa un cambio legítimo posterior | Rollback condicional con comprobación previa |
| Puntuación engañosa por controles no evaluados | Cobertura como salida de primera clase; pendiente ≠ aprobado |
| Doble penalización de una misma causa | Deduplicación por causa raíz antes de puntuar |
| Sesgo contra productos nuevos en la priorización | Demanda potencial además de histórica; backlog separado de la deuda |
| Prometer escrituras que Magento no soporta | Alcance real declarado por operación (caso `is_filterable`) |
| Coste de IA fuera de control | Reglas primero, triage, caché, modelo por tarea, presupuesto con parada dura |
| Coste y carga de la sonda | Muestreo estratificado dirigido por impacto; respeto de límites de tasa |
| Custodiar catálogos ajenos | Aislamiento, cifrado, borrado verificable, auditoría inmutable |
| Nadie usa una lista de 40.000 hallazgos | Priorización desde S3; causa raíz en vez de síntoma repetido |

---

## 13. Preguntas abiertas

1. **Versión y edición de Magento** (Open Source / Adobe Commerce), on-prem o cloud.
   Condiciona el módulo y los límites de la API. **Bloquea S0.**
2. Presupuesto y tolerancia de coste de IA por mes.
3. Acceso a GA4 por tenant: quién lo concede y cómo.
4. ¿Se instrumentará el registrador con sello de origen y flag de modo rápido?
5. Problemas de catálogo aún no enumerados.
6. Confirmar idiomas, monedas e impuestos por store view (PY / BR).
7. Requisitos de residencia de datos de los 5 tenants.
8. Nombre: **decidido — StandardSkudo**, dentro de la familia `Standard_*`. Pendiente y
   no bloqueante: dominio, marca en DINAPI (Paraguay) e INPI (Brasil), y lectura en voz
   alta por un hablante de portugués brasileño.
9. **Benchmark entre tenants** anonimizado: mejora las sugerencias del eje 11, exige
   adhesión explícita y garantías de agregación. Con 5 tenants un agregado puede ser
   reidentificable: definir el umbral mínimo antes de construirlo.
10. **Criterio editorial de descripción corta** (eje 6). Una revisión externa lo atribuyó
    a preferencias previas del cliente guardadas en memoria; se buscó y **no existe tal
    registro**. Queda sin codificar hasta que el criterio se confirme de primera mano.
11. Configuración de transportistas y sus divisores volumétricos por mercado (eje 4).
12. ¿Existe ya una taxonomía de estados de negocio —preventa, reposición,
    descatalogado— o hay que crearla? (eje 8)

---

## 14. Registro de revisiones

**Revisión 2 — 2026-09-09.** Incorpora una revisión técnica externa. Cambios de fondo:

- Corregido el modelo de asignación de categorías: es **global** en el core de Magento
  (`catalog_category_product` no tiene `store_id`); el efecto por tienda se deriva
- Corregida la operación 5: `is_filterable` es propiedad del **atributo**, no de la
  categoría; la propuesta puede ser por categoría, la escritura no
- Corregida la consolidación de opciones: por `option_id`, no por etiqueta.
  `Negro`/`Preto` pueden ser la misma opción traducida
- Corregida la aritmética del ejemplo del eje 4, y la sospecha de conversión pasa a
  exigir evidencia adicional en lugar de corregir por limpieza del factor
- Añadida la **sonda de superficie publicada**: el eje 9 no es derivable del espejo
- Añadido **control de concurrencia** en la remediación (*lost update*), verificación
  por **lectura independiente** y **rollback condicional**
- Añadidos **cuatro estados del dato**, **cobertura**, **errores críticos**,
  **deduplicación por causa raíz** y **falsos positivos medidos por regla**
- Añadida **evidencia por dato y por corrección** como control transversal (S2)
- Reformulado el eje 10: controles medibles con justificación independiente, sin
  afirmar mecanismos de citación en LLMs; posicionamiento movido a **coherencia
  verificable y encontrabilidad medida**
- Añadido el eje 12, **encontrabilidad efectiva**
- Compatibilidad entre productos y aptitud por canal documentadas como fuera de alcance
- Priorización con confianza, esfuerzo y demanda potencial, corrigiendo el sesgo contra
  productos nuevos
- Reordenados los sub-proyectos: precisión → evidencia → escritura segura → medición
  externa → IA

---

## Fuentes

- magendooro/magento2-catalog-quality — https://github.com/magendooro/magento2-catalog-quality
- Google, AI Features and Your Website — https://developers.google.com/search/docs/appearance/ai-features
- Magento 2, asignación producto–categoría sin store scope — https://github.com/magento/magento2/issues/32925
- Ivashchenko, atributos filtrables en layered navigation — https://medium.com/@sivaschenko/magento-2-layered-navigation-filterable-attributes-4532e3cfd276
- Adobe Commerce, module-catalog — https://developer.adobe.com/commerce/php/module-reference/module-catalog
- Akeneo, mejores PIM 2026 — https://www.akeneo.com/blog/best-pim-2026/
- Salsify vs Akeneo — https://www.selecthub.com/pim-software/salsify-vs-akeneo/
- Digital shelf analytics — https://theretailexec.com/tools/best-digital-shelf-analytics-software/
- Syndigo Product Content Analytics — https://syndigo.com/analytics/
- Herramientas GEO para ecommerce — https://www.width.ai/post/the-best-generative-engine-optimization-tools-for-ecommerce
- Amasty Mass Product Actions — https://amasty.com/mass-product-actions-for-magento-2.html
- Extensiones SEO Magento 2 — https://www.magedelight.com/blog/best-magento-2-seo-extensions-comparison/
