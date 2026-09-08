# Skudo — Plataforma de Calidad de Catálogo

**Producto:** Skudo (`SKU` + *escudo*; funciona igual en español y portugués)
**Fecha:** 2026-09-08
**Estado:** diseño aprobado en sus secciones 1 y 2; pendiente de revisión completa

---

## 1. Resumen ejecutivo

SaaS multitenant que evalúa y corrige la calidad del registro de productos en tiendas
Magento, a nivel de *store view*, combinando calidad de datos de catálogo, SEO y
GEO-readiness en un único objeto de análisis, con remediación masiva bajo aprobación
humana y verificación de que el cambio realmente se aplicó.

El sistema se instala como un módulo en el Magento del cliente, mantiene un espejo
canónico del catálogo, infiere las reglas de calidad a partir del propio catálogo,
puntúa cada registro, prioriza los hallazgos por impacto comercial real y propone
lotes de corrección que un humano aprueba antes de que se escriban.

**Tenant piloto:** Nissei — catálogo de cientos de miles de SKUs, dos store views
(Paraguay y Brasil), ~100 attribute sets, ritmo de cambio constante.
**Objetivo comercial:** 5 tenants en el primer año.

---

## 2. El problema

El catálogo se registra mediante un sistema propio de carga por lotes que a veces
produce registros de baja calidad ("registro rápido"): nombres que son códigos
internos, categorías equivocadas, atributos vacíos, pesos y dimensiones con errores
de escala, datos guardados en el campo equivocado, productos sin imagen.

Dos consecuencias del diseño se derivan de esto:

1. **Es un problema de flujo, no de stock.** Hay un proceso que produce defectos de
   forma continua. Un sistema que solo corrija aguas abajo es una cinta de correr.
   Por eso el diseño incluye medición de calidad en el origen y validación
   pre-registro.
2. **El registrador también reescribe productos existentes**, así que puede pisar
   las correcciones aplicadas. La detección de regresión es obligatoria, no opcional.

El coste no es estético. Un atributo filtrable vacío es un producto que el cliente no
puede encontrar. Un peso mal cargado es un flete mal cotizado. Son pérdidas
cuantificables, y el producto debe expresarlas en esos términos.

---

## 3. Estado del mercado y posicionamiento

Investigación realizada el 2026-09-08. No existe ningún producto que cubra la
intersección buscada. El mercado se divide en cinco categorías, cada una con un hueco
estructural:

| Categoría | Ejemplos | Hueco |
|---|---|---|
| PIM con data quality | Akeneo (Data Quality Insights, grados A–E), Salsify, Sales Layer, inriver | El PIM quiere *ser* el master: exige migrar el catálogo. No audita Magento in situ. Sin SEO ni GEO. |
| Digital Shelf Analytics | Profitero, DataWeave, Syndigo Content Health, Stackline, NIQ | Auditan tus productos en retailers *ajenos*. Óptica de marca/CPG. Sin write-back. |
| SEO | Semrush, Amasty SEO Toolkit, Mageplaza SEO Reports, AuditIQ | SEO a nivel página, no semántica a nivel atributo ni por tipo de producto. |
| GEO / AEO | Scrunch, AthenaHQ, Nudge, Pumice, geoaudit.co | Miden citación de marca, no calidad de registro. Pumice es el más cercano pero no es Magento-native ni multitenant. |
| Bulk edit Magento | Amasty Mass Product Actions, Meetanshi, Magefan | Músculo de ejecución sin criterio: no detecta ni prioriza. |

**Arte previo más relevante:** `magendooro/magento2-catalog-quality` (open source, Magento 2).
Product Health Score 0–100, grados A–E, ejes Enrichment + Consistency, detector
*filter-blind*. Es módulo mono-tienda, sin SEO/GEO, sin IA, sin multitenancy y sin
remediación. **Su modelo de scoring debe leerse como referencia antes de implementar S1.**

**Nuestra posición defendible** es la intersección de cinco cosas que nadie junta:

1. Audita el catálogo donde vive, a scope de store view, sin migrar nada.
2. Puntúa por tipo de producto, con reglas inferidas del catálogo.
3. SEO y GEO sobre el mismo registro, no como herramienta aparte.
4. Remediación masiva con aprobación, verificación y rollback.
5. Multitenant × multi-store-view.

Y dos que ninguna herramienta investigada tiene, porque todas asumen que el catálogo
simplemente existe: **medición de calidad en el origen del registro** y **detección
de regresión**.

---

## 4. Decisiones tomadas

| Decisión | Elección | Razón |
|---|---|---|
| Audiencia | SaaS multitenant desde el inicio | Producto vendible a 5 tenants. Consecuencia: no se puede contar con acceso a la base de datos del cliente. |
| Conector | Módulo Magento instalable + API | Lectura masiva eficiente, deltas/webhooks, write-back en bloque. La REST estándar es demasiado lenta para 400k registros. |
| Persistencia | Espejo canónico en Postgres | Re-scoring barato al iterar reglas, base para rollback, perfilado estadístico, historia. |
| Origen de las reglas | Inferidas del catálogo + curación humana + piso externo obligatorio | 100 attribute sets × 5 tenants hacen imposible la configuración manual. El piso externo evita aprender el error sistemático como norma. |
| Alcance GEO | GEO-readiness sobre el registro | Escala a 400k registros. Evita construir un subsistema de scraping de LLMs. |
| Remediación | Siempre con aprobación humana | El primer catálogo de un cliente destrozado por un bug mata el producto. |
| Priorización | Ventas + búsqueda interna + GA4 + stock/margen | Sin peso comercial, 40.000 hallazgos son una lista inservible. |
| UI | App SaaS propia (Next.js) | Coherente con multitenancy; no depender de la UI de Adobe. |
| Stack | Python + Postgres + Next.js; módulo en PHP | La parte difícil es procesamiento de datos e IA a escala. |
| Aislamiento de cómputo | Cola y worker por tenant | Con 5 tenants grandes, el pipeline compartido no aporta nada y complica el borrado. |

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
 │   registro (S3)    │            │ Postgres             │
 └────────────────────┘            └──────┬───────────────┘
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
                                 │ PHS + Impacto │   │ Next.js      │
                                 └───────────────┘   └──────┬───────┘
                                 ┌───────────────┐          │
                                 │ Remediación   │◄─────────┘
                                 │ lote/diff/RB  │  aprobación humana
                                 └───────────────┘
```

### 5.2 El ciclo cerrado de verificación

Cuando se aprueba un lote y el módulo escribe el cambio, ese cambio **vuelve como
delta**. El sistema no confía en su propia escritura: la verifica leyendo la realidad
de Magento y re-evaluando el registro. Si un write-back falló silenciosamente —scope
equivocado, atributo no aplicable al set, valor rechazado— el score no mejora y el
lote queda marcado como parcialmente aplicado.

La verificación es estructural, no un chequeo que alguien deba recordar hacer.

### 5.3 El módulo Magento

No contiene lógica de calidad. Solo mueve datos:

- **Lectura masiva** paginada por cursor (nunca por offset, que degrada en catálogos
  grandes), con proyección de campos.
- **Cola de cambios**: observadores que registran qué entidades cambiaron, para servir
  deltas desde un watermark.
- **Señales comerciales** agregadas: ventas por SKU y store view, búsquedas internas
  y sus resultados, stock, margen.
- **Escritura por lote idempotente**, con clave de idempotencia por ítem, y respuesta
  detallada por ítem (aplicado / rechazado / sin cambio) en lugar de un OK global.
- **Validación pre-registro** (S3): expone el motor de reglas como portero.

Toda la inteligencia vive del lado que podemos desplegar cuando queramos, sin pedir a
un cliente que actualice nada.

### 5.4 El objeto central

Todo gira alrededor del **registro evaluable**: `(tenant, producto, store_view)`.
No `(tenant, producto)`.

Un producto no tiene un score: tiene dos, uno en PY y otro en BR. Su nombre en
portugués puede estar mal aunque en español esté perfecto; su categoría puede estar
bien asignada en un website y ausente en el otro. Esta es exactamente la dimensión
que las herramientas del mercado ignoran.

### 5.5 Modelo de datos

Cinco grupos, con una regla de oro: **el espejo y las señales son la única fuente de
verdad; todo lo demás se recalcula.**

**Espejo** (reflejo de Magento; se sobrescribe, nunca se edita a mano)
- `product_records` — una fila por `(tenant, sku, store_view)`. Atributos efectivos
  resueltos en JSONB, más la procedencia de scope (qué valor viene de global, de
  website o de store view). Guardar la procedencia es imprescindible: corregir en el
  scope equivocado es el error de write-back más común en Magento.
- `product_categories` — asignaciones por store view.
- `categories` — árbol, nombres por store view, flags de anchor.
- `attribute_sets`, `attributes` — código, tipo, scope, `is_filterable`, requerido,
  opciones de dropdown.
- `media_assets` — roles, dimensiones, peso, hash perceptual (para duplicados), alt.
- `product_relations` — configurables e hijos, atributos de variación.

**Señales** (ritmo de actualización distinto; opcionales por tenant)
- `signal_sales`, `signal_search`, `signal_analytics`, `signal_inventory` —
  agregados por SKU y store view, con ventana temporal.

**Reglas** (versionadas: hay que saber con qué versión se calculó un score)
- `rules` — tenant, attribute set o tipo, eje, definición JSONB, confianza,
  evidencia (% de catálogo que la respalda), estado (borrador/aprobada/rechazada),
  origen (inferida / piso externo / manual).
- `rule_versions`, `ruleset_snapshots`.

**Hallazgos y scores** (todo derivado y desechable)
- `findings` — eje, regla, severidad, evidencia JSONB, detectado, resuelto, estado, y
  **scope del hallazgo**: `product` | `attribute_set` | `attribute` | `category` |
  `store`. No todos los hallazgos cuelgan de un producto: los del eje 11 cuelgan de un
  attribute set o de un par `(categoría, atributo)`. Un solo hallazgo de configuración
  puede explicar miles de hallazgos de producto, y la UI debe poder mostrar esa
  relación de causa en lugar de repetir el síntoma 5.000 veces.
- `scores` — PHS, grado, sub-scores por eje, impacto, versión de ruleset, timestamp.

**Remediación** (registro de auditoría; esto sí es inmutable)
- `remediation_batches`, `remediation_items` (tipo de operación, campo, antes,
  después, confianza, estado), `write_results`, `snapshots`.
- `regression_events`.
- `registration_cohorts` — cohortes de registro inferidas.

### 5.6 Fronteras entre componentes

- El **motor de reglas es una función pura**: recibe un registro más un conjunto de
  reglas y devuelve hallazgos. No consulta la base, no llama a Magento, no llama a la
  IA. Trivial de testear con casos fabricados — imprescindible cuando la lógica va a
  tener cientos de reglas y va a cambiar cada semana.
- La **capa IA** se aísla detrás de una interfaz estrecha
  (`analizar(registro, tipo_de_análisis) → hallazgos`) con caché por hash de contenido
  delante. Cambiar de modelo o proveedor no toca nada más.
- El **perfilador** solo lee el espejo y escribe reglas en borrador. No puntúa.
- El **scorer** solo lee hallazgos y señales. No detecta nada.

---

## 6. El motor de calidad

### 6.1 Los once ejes

Cada hallazgo pertenece a un eje, tiene severidad, y puede no aplicar según el tipo
de producto: a un tornillo no se le exigen seis imágenes ni una ficha de 300 palabras.

**Eje 1 — Identidad y nomenclatura**
- El nombre no sigue la plantilla de su tipo (ver 6.2: plantilla de nombre)
- El nombre *es* un código, no un nombre (`INV-WIFI89283942`)
- Basura en el nombre: código interno o SKU embebido, `NUEVO!!!`, MAYÚSCULAS,
  HTML, dobles espacios, caracteres de control
- Nombre sin traducir para la store view
- Marca ausente o inconsistente (`Samsung` / `SAMSUNG` / `Sansung`)
- Modelo ausente, o que contradice el nombre
- Duplicados reales: mismo producto registrado varias veces (por GTIN, por
  marca+modelo, por similitud de nombre)
- GTIN/EAN ausente, formato inválido, dígito de control incorrecto, compartido
- Error de tipología: debería ser variante y está suelto; configurable sin hijos;
  variantes que no comparten atributos de variación

**Eje 2 — Categorización**
- Sin categoría, solo en la raíz, o solo en una categoría cajón de sastre
- Categoría demasiado genérica existiendo una hoja adecuada
- Categoría incoherente con el producto (centrifugador en Secarropas) — IA
- Accesorio catalogado como producto principal (mouse pad en Mouses) — IA + política
  del tenant, porque a veces es merchandising deliberado
- Sobre-categorización que diluye la navegación
- Asignación inconsistente entre store views
- Salud del árbol: categorías vacías, duplicadas, profundidad excesiva

**Eje 3 — Atributos**
- Obligatorios del set vacíos, según regla inferida y curada
- **Filter-blind**: atributo filtrable vacío ⇒ el producto no aparece cuando el
  cliente usa los filtros. El hallazgo con traducción más directa a dinero perdido
- **Sin poder discriminante**: el atributo tiene prácticamente el mismo valor en todo
  su tipo, así que no informa; ensucia el nombre y genera un filtro de una sola
  opción (el color "negro" en cámaras profesionales). Métrica inversa del
  filter-blind, sale del mismo cálculo del perfilador
- Valores basura: `N/A`, `-`, `0`, `SIN DATO`, `.`, `xx`
- Unidades ausentes o mezcladas dentro del mismo atributo
- Opciones de dropdown casi duplicadas: `Negro` / `negro` / `Black` / `Preto`
- Atributos poblados que no aplican al tipo (ruido en filtros)
- Attribute set equivocado para el producto — IA
- Contradicción atributo ↔ nombre (el nombre dice 55", el atributo 50")
- Atributos traducibles sin traducir

**Eje 4 — Plausibilidad física** *(eje propio por su impacto económico directo)*

Un peso mal cargado no es un defecto de calidad: es un flete mal cotizado. Es el
defecto más fácil de traducir a guaraníes de todo el catálogo.

- **Error de escala por orden de magnitud.** Contra la distribución del mismo tipo:
  2000 kg donde las notebooks se agrupan en 1,5–3 kg es un factor exacto de 1000
  (gramos cargados como kilos); 400×203×302 contra 40×23×30 es un factor de 10
  (milímetros como centímetros). Que el factor sea limpio es lo que da confianza: el
  sistema no adivina el valor correcto, **deduce la corrección** y la propone con
  evidencia
- **Chequeo de densidad.** Peso contra volumen: 2000 kg en 40×23×30 cm es una
  densidad imposible. Atrapa los dos campos a la vez y funciona incluso sin conocer
  el tipo de producto
- **Peso volumétrico contra peso real**: literalmente la fórmula con la que el
  transportista cobra
- Dimensiones ausentes en productos que se envían; valores cero o negativos

**Eje 5 — Descripción larga**
- Ausente, insuficiente para informar, o desproporcionadamente larga
- HTML roto, estilos inline, restos de pegado desde Word, tablas destrozadas
- Texto duplicado: la misma descripción en cientos de SKUs
- Descripción de otro producto por copy-paste — IA
- Contradice los atributos o el nombre — IA
- No menciona la información decisiva de compra que sí está en los atributos
- Idioma incorrecto para la store view
- Precios, promociones o plazos escritos en el texto: caducan y el producto miente
- Códigos de referencia en la descripción que pertenecen a un atributo o al short
- Keyword stuffing, legibilidad

**Eje 6 — Short description**
- Ausente, o copia literal de la larga
- No cumple su función: debe contener la razón de compra en una frase
- Longitud fuera del rango útil para donde se muestra (grid, comparador, feeds)

**Eje 7 — Media**
- Sin imagen, o solo un placeholder
- Menos imágenes de las que su tipo necesita
- Resolución insuficiente, proporciones inconsistentes, peso excesivo
- Falta imagen por rol (base / small / thumbnail / swatch)
- Alt text ausente o igual al nombre del archivo
- Imagen reutilizada entre productos distintos (hash perceptual)
- Variantes de color sin foto propia
- Marca de agua o texto promocional quemado en la imagen

**Eje 8 — Coherencia comercial**
- Precio 0 o ausente; precio especial caducado; incoherencia de precio o moneda
  entre PY y BR
- Sin stock pero visible; con stock pero deshabilitado
- Visibilidad, estado y websites asignados incoherentes entre store views

**Eje 9 — SEO por store view**
- Meta title y description ausentes, duplicados, fuera de longitud
- URL key ausente, con basura, duplicada, o cambiada sin redirect (404 heredados)
- Canonical, indexabilidad, y hreflang PY ↔ BR
- Structured data `Product` / `Offer` / `Brand` completo y válido
- Contenido duplicado entre store views: el mismo texto en los dos mercados
- El término por el que la gente busca ese producto no aparece en el nombre ni en el
  title (cruce con la señal de búsqueda interna)

**Eje 10 — GEO-readiness**
- Los atributos decisivos están expresados en el texto, no solo en la tabla: un LLM
  no lee la ficha técnica como la lee un humano
- El nombre identifica un producto único y citable (desambiguación de modelo)
- Afirmaciones factuales y verificables en lugar de marketing hueco
- Structured data completo **y coherente con el texto visible**: la contradicción
  entre schema y contenido es lo que más destruye la confianza de un modelo
- Cobertura de preguntas reales de compra ("¿alcanza para 20 m²?", "¿es compatible
  con X?")
- Entidades reconocibles: marca, compatibilidades, estándares, certificaciones
- **Consistencia global** entre nombre, atributos, descripción y schema. Es la señal
  que más pesa, y la que un catálogo con registro rápido rompe siempre

**Eje 11 — Diseño del catálogo** *(no evalúa productos: evalúa la configuración)*

Los diez ejes anteriores evalúan registros. Este evalúa el **modelo de datos y la
navegación**, y sus hallazgos no se resuelven corrigiendo productos sino cambiando la
configuración de la tienda. Un producto no puede tener bien un atributo que el
attribute set no define.

- **Atributos sugeridos para el tipo de producto**: atributos que ese tipo debería
  tener y que el attribute set **ni siquiera define**. No es un producto incompleto:
  es un modelo de datos incompleto. Cuatro fuentes de evidencia:
  1. *Piso externo por categoría* — atributos que Google Shopping y GS1 esperan para
     esa categoría de producto
  2. *Búsqueda interna* — la fuente más valiosa: si los clientes buscan "notebook
     ssd" y no existe atributo de tipo de disco, la demanda está probando la carencia
     con dinero real. Términos buscados que no corresponden a ningún atributo
     existente son atributos faltantes
  3. *Texto del propio catálogo* — especificaciones que aparecen de forma consistente
     en los nombres y descripciones de ese tipo pero no existen como atributo
     estructurado. El perfilador ya lee ese texto
  4. *Benchmark anonimizado entre tenants* — solo con adhesión explícita del tenant y
     de forma agregada. Ver pregunta abierta 9
- **Filtro perdido**: atributo con alto poder discriminante y buena cobertura que
  **no está marcado como filtrable** en las categorías donde vive. El dato existe, la
  demanda existe, y el cliente no puede filtrar por él. Es el complemento exacto del
  filter-blind: uno detecta *falta de dato que impide filtrar*, este detecta *dato
  bueno que nadie puso a filtrar*
- **Filtro inútil**: atributo marcado como filtrable que en esa categoría tiene un
  solo valor efectivo, o cobertura tan baja que filtrar por él esconde el catálogo.
  Ocupa espacio en la navegación y no filtra nada
- **Sugerencia por categoría, no global.** La navegación por capas es por categoría,
  así que la recomendación se emite por `(categoría, atributo, store view)`. "Pulgadas"
  debe filtrar en Televisores y no tiene sentido en Perfumería, aunque el atributo sea
  el mismo
- **Orden y posición de los filtros** según demanda de búsqueda interna: el filtro más
  buscado debería estar arriba
- **Salud de los attribute sets**: sets solapados o redundantes, sets "Genérico"
  sobrecargados que agrupan productos que merecen set propio, categorías cuyos
  productos están repartidos entre sets incompatibles

Advertencia de riesgo: marcar un atributo como filtrable tiene coste de rendimiento en
el índice de navegación por capas de Magento, y el radio de impacto es todo el
storefront, no un producto. Por eso estas correcciones viajan en una **vía de
aprobación separada** (ver 7.1, operación 5).

**Eje transversal — Proceso** *(no evalúa el producto: evalúa cómo se registra)*
- **Edad del registro y periodo de gracia.** Un producto registrado hace tres horas
  en modo rápido está incompleto por diseño: es trabajo pendiente, no un defecto. Uno
  de hace dos años igual de incompleto sí lo es. Dos colas distintas, para dos
  personas distintas: **backlog de enriquecimiento** frente a **deuda de calidad**.
  Mezclarlas en una lista de 40.000 filas es lo que hace inútiles a las herramientas
  del mercado
- **Regresión**: campos corregidos que volvieron a degradarse. Obligatorio, dado que
  el registrador reescribe productos existentes
- **Cohortes de registro**: agrupación por proximidad temporal y firma de defectos,
  para señalar tandas malas incluso sin traza de origen
- **Calidad en el origen**: si el registrador sella lote/usuario/modo, atribución de
  calidad por proceso y por persona
- **Velocidad**: ¿el backlog se drena o crece? Es el único número que le dice a
  dirección si el problema se está resolviendo

### 6.2 Reglas: inferencia, curación, piso externo

**El perfilador** analiza el espejo por attribute set y propone reglas en borrador:

- Cobertura de atributos: si el 95% de los productos de un set tienen BTU relleno, el
  5% restante son anomalías, no una excepción legítima
- Distribuciones de valores numéricos por tipo: base de la detección de escala y de
  los rangos plausibles
- Poder discriminante de cada atributo: base del hallazgo de atributo inútil
- **Plantilla de nombre por tipo**: qué atributos aparecen en los nombres bien
  formados de ese set y en qué orden. `Aire Acondicionado Inverter Wifi 18.000 BTU`
  no es una frase, es `tipo + tecnología + conectividad + capacidad`. La plantilla se
  deduce de los nombres buenos e incluye solo atributos con poder discriminante — así
  el sistema nunca propone meter "negro" en el nombre de una cámara profesional, pero
  sí en el de una funda de celular, donde decide la compra
- Formatos y unidades canónicas por atributo
- Longitudes típicas de descripción y short por tipo
- **Atributos faltantes en el set**: cruce del piso externo por categoría, de los
  términos de búsqueda interna sin atributo correspondiente, y de las especificaciones
  que aparecen en el texto del tipo sin existir como atributo estructurado
- **Candidatos a filtrable por categoría**: para cada par `(categoría, atributo)`,
  cobertura × poder discriminante × demanda en búsqueda interna, contra el estado
  actual de `is_filterable`. Produce las dos caras: filtro perdido y filtro inútil

Cada regla propuesta lleva **confianza** y **evidencia** (qué porcentaje del catálogo
la respalda), para que la curación humana vea de dónde salió antes de aprobarla.

**El piso externo** es obligatorio y no se infiere: los campos exigidos por Google
Shopping, Meta catalog y GS1. Impide que el sistema aprenda un error sistemático como
norma — si el 100% del catálogo carece de GTIN, la inferencia diría que GTIN no hace
falta, y el piso externo dice que sí.

**La curación** es una UI donde el operador acepta, ajusta o rechaza reglas por lote,
viendo ejemplos reales de productos que cada regla marcaría.

### 6.3 Capa IA y control de coste

400k registros hacen imposible pasar IA por todo el catálogo en cada evaluación. El
diseño es en capas, y el principio es simple: **nunca llamar a la IA para algo que una
regla puede decidir.**

1. **Reglas deterministas sobre el 100%** del catálogo. Baratas, corren siempre.
2. **Triage**: la IA se invoca solo sobre registros que las reglas marcan como
   sospechosos, o sobre SKUs de alto impacto comercial.
3. **Caché por hash de contenido**: si el nombre, atributos y descripción relevantes
   no cambiaron, no se vuelve a pagar por analizar ese registro. Con "ritmo de cambio
   constante", los deltas dejan de ser una optimización y pasan a ser el corazón del
   sistema.
4. **Modelo por tarea**: modelo económico para clasificación (categoría, attribute
   set), modelo más capaz solo para generación de texto.
5. **Presupuesto por tenant y por mes**, con parada dura y visible en la UI.

Tareas que hace la IA:
- Extracción de atributos desde el texto existente
- Clasificación de categoría y de attribute set
- Detección de incoherencias nombre ↔ atributos ↔ descripción
- Detección de descripción perteneciente a otro producto
- Generación de nombre (solo cuando la plantilla no puede rellenarse con atributos),
  descripción y short
- Evaluación de cobertura de preguntas de compra (eje 10)

**Nota sobre extracción de atributos.** Es la capacidad más rentable del sistema
entero, y a menudo ni siquiera necesita IA. En un catálogo así, la RAM y las pulgadas
normalmente ya están escritas en el nombre o la descripción (`Notebook Lenovo 8GB
15.6"`): el dato no falta, está mal guardado. La extracción no inventa nada, solo
estructura lo que ya existe — por eso es de máxima confianza. Y alimenta la
reconstrucción de nombres: se extraen los atributos del nombre viejo y con ellos se
rellena la plantilla. El texto malo contiene la materia prima para arreglarse.

Orden de preferencia por atributo: regex/parser específico → IA de extracción → nada.

### 6.4 Scoring

Tres niveles, para tres personas distintas:

- **PHS 0–100 y grado A–E** por `(producto, store view)` — para quien arregla productos
- **Sub-score por eje** — para quien decide dónde invertir: *"bien en atributos, en el
  suelo en GEO"*
- **Impacto comercial** — el orden real de trabajo

El PHS es una suma ponderada de los sub-scores por eje. Cada sub-score parte de 100 y
resta penalizaciones por severidad de hallazgo, con tope. Los pesos de cada eje se
definen **por tipo de producto**, no globalmente. El grado nunca se calcula global:
siempre por store view.

Todo score guarda la versión de ruleset con la que se calculó.

### 6.5 Priorización por impacto comercial

Sin peso comercial, el sistema produce una lista de decenas de miles de filas que
nadie mira: da igual que un SKU que vende 300 unidades al mes y uno que no vende nunca
tengan los dos grado E.

`impacto = severidad × efecto_en_descubribilidad × peso_comercial`

El `peso_comercial` es una mezcla normalizada de: facturación y unidades del SKU,
demanda en búsqueda interna, tráfico sin conversión (GA4), disponibilidad de stock y
margen. Diseñado para **degradar con gracia**: funciona solo con ventas, mejora con
búsqueda interna, y con GA4 detecta el caso más valioso de todos —mucho tráfico, poca
conversión, registro pobre.

Regla de producto: el stock y el estado de descatalogado filtran antes de gastar IA.
En un catálogo de 200k, enriquecer productos agotados o descatalogados es dinero
quemado.

---

## 7. Remediación

### 7.1 Cuatro tipos de operación

1. **Rellenar** — poner un valor donde no hay ninguno (extracción, inferencia, IA)
2. **Normalizar** — corregir formato, unidad, capitalización, consolidar opciones de
   dropdown casi duplicadas
3. **Reubicar** — el valor es correcto y está en el campo equivocado. Sacar
   `cf23489243-1/12` del nombre y ponerlo en el atributo de referencia. **Debe ser
   atómico**: quitar el código del nombre sin haberlo guardado antes es destruir
   información
4. **Reconstruir** — regenerar el campo entero desde la plantilla del tipo más los
   atributos (nombres), o generarlo (descripción, short)
5. **Reconfigurar** — cambiar la estructura, no el dato: crear un atributo que falta
   en un set, marcar un atributo como filtrable en unas categorías, retirar un filtro
   inútil, reordenar filtros, mover productos a un set más adecuado

   Las cuatro primeras operaciones tocan productos; esta toca la tienda entera. Un
   filtro mal activado degrada la navegación y el índice de layered navigation de todo
   el storefront. Por eso viaja en una **vía de aprobación separada**, con aprobador de
   nivel administrador, estimación de radio de impacto (cuántos productos y categorías
   afecta), y despliegue por etapas: primero una categoría, se verifica, después el
   resto.

### 7.2 Ciclo de un lote

```
propuesta → preview + diff → aprobación humana → snapshot →
escritura por lote → delta de vuelta → re-evaluación → resultado por ítem
                                                            │
                                                     rollback disponible
```

- **Preview**: diff campo por campo, muestra de ejemplos representativos, conteo
  afectado, estimación de impacto en el score y en el impacto comercial
- **Aprobación humana obligatoria.** Nada se escribe sin un clic. Es la decisión que
  hace vendible el producto: el primer catálogo de un cliente destrozado por un bug
  mata la empresa
- **Snapshot** del estado anterior de los campos afectados, antes de escribir
- **Escritura idempotente** con resultado por ítem, no OK global
- **Verificación por delta**: el efecto real se lee de Magento, no se asume
- **Rollback** al snapshot, por lote o por ítem

El write-back debe escribir **en el scope correcto**. Corregir un valor global cuando
el problema era del store view de BR, o al contrario, es el error más común en
Magento; por eso el espejo guarda la procedencia de scope de cada atributo.

### 7.3 Detección de regresión

Obligatoria, porque el registrador por lotes también reescribe productos existentes.

El espejo tiene historia, así que el sistema puede detectar algo imposible sin espejo:
*"este campo se corrigió el 12 de marzo y volvió a quedar vacío el 3 de abril"*. Eso
no es un hallazgo nuevo: es una **alerta de que algo aguas arriba está pisando las
correcciones**. Sin esto, se corrigen 5.000 productos y nadie se entera de que al mes
siguiente 900 volvieron atrás.

Las regresiones se agrupan por cohorte y por campo, para poder señalar la causa
aguas arriba en lugar de volver a corregir de uno en uno.

---

## 8. Validación pre-registro (el portero)

Lo más rentable del sistema. Corregir 200.000 registros cuesta dinero cada vez;
impedir que entre uno malo cuesta una llamada de validación.

Como el sistema de registro por lotes es propio, el mismo motor de reglas se expone
como endpoint de validación: recibe un lote antes de que entre al catálogo y devuelve
*"este lote entraría con grado D; faltan estos 4 atributos obligatorios del set; el
peso de estos 12 productos está fuera de rango plausible"*.

Cero lógica nueva —es el mismo motor de reglas puro de 5.6— y convierte el producto de
limpiador en portero.

**Instrumentación recomendada del registrador** (dos cambios pequeños, retorno grande,
ninguno bloqueante):
- **Sellar el origen**: lote, usuario y modo de registro en cada producto creado.
  Desbloquea toda la atribución de calidad en el origen
- **Marcar el modo rápido**: un flag que diga "esto entró incompleto a propósito". Sin
  él, la juventud del registro se infiere por fecha de creación, que funciona pero
  confunde "nuevo e incompleto" con "viejo y roto" en los casos de borde

---

## 9. Multitenancy, seguridad y custodia

Custodiamos el catálogo de nuestros clientes. Eso no es un detalle de
infraestructura, es el riesgo principal del negocio.

- **Aislamiento de datos** por tenant a nivel de fila, con verificación en la capa de
  acceso, no solo en la consulta
- **Cola y worker por tenant**: el re-scan de uno no bloquea a los demás
- **Credenciales del módulo por tenant**, rotables, con permisos mínimos; separadas
  las de lectura de las de escritura
- **Cifrado** en reposo y en tránsito; secretos fuera del código
- **Borrado verificable** al cancelar: espejo, señales, hallazgos, scores y snapshots.
  El registro de auditoría se conserva anonimizado el tiempo que exija la ley
- **Roles**: operador de catálogo (ve y propone), aprobador (escribe), administrador
  (reglas y presupuesto), solo-lectura (dirección)
- **Log de auditoría inmutable** de toda escritura al catálogo del cliente, con quién
  aprobó qué y cuándo
- **Presupuesto de IA por tenant** con parada dura
- Residencia de datos: PY y BR. A verificar si alguno de los 5 tenants tiene
  requisitos regulatorios de localización

---

## 10. Explícitamente fuera de alcance

- Monitoreo de citaciones en LLMs (ChatGPT, Perplexity, AI Overviews). Decisión
  tomada: GEO significa readiness sobre el registro. Reconsiderable en un futuro
  sub-proyecto
- Ser el master del dato. No somos un PIM: Magento sigue siendo la fuente
- Plataformas distintas de Magento
- Escritura automática sin aprobación humana
- Traducción masiva PY↔BR como producto (se detecta la falta, no se asume que la
  traducción es nuestro trabajo)
- Optimización de precios o gestión de stock

---

## 11. Descomposición en sub-proyectos

Cada sub-proyecto tiene su propio ciclo spec → plan → implementación. Multitenancy se
**diseña** desde S0 (en el modelo de datos) y se **construye** al final.

### S0 — Conector y espejo canónico
Módulo PHP (lectura masiva por cursor, cola de cambios, deltas desde watermark,
señales, escritura por lote idempotente). Ingestor por tenant. Espejo con procedencia
de scope. Reconciliación y detección de deriva.

*Aceptación:* espejo de 200k SKUs × 2 store views sincronizado; un cambio manual en
Magento aparece en el espejo dentro del SLA de delta; re-sync completo dentro de la
ventana acordada; procedencia de scope correcta en una muestra verificada a mano.

### S1 — Perfilador, motor de reglas y PHS
Perfilador que propone reglas con confianza y evidencia. Piso externo. UI de curación.
Motor determinista puro. Hallazgos, PHS, grados, sub-scores.

Detectores deterministas de bandera: filter-blind, poder discriminante, plausibilidad
física (escala + densidad + volumétrico), plantilla de nombre, duplicados, campos
basura, unidades.

Incluye el eje 11 en modo **solo detección**: atributos sugeridos por tipo, filtros
perdidos y filtros inútiles por categoría. Detectar aquí es casi gratis —el perfilador
ya calcula cobertura y poder discriminante— y aplicar se deja para S2.

*Aceptación:* catálogo entero puntuado; en una muestra ciega, el equipo de Nissei
confirma que los grados coinciden con su percepción; los tres detectores de
plausibilidad encuentran casos reales verificables; las reglas inferidas para los 10
attribute sets más grandes se curan en una sesión de trabajo, no en semanas; el informe
de atributos sugeridos y de filtros perdidos para las 20 categorías con más tráfico es
revisado por el equipo de catálogo y considerado accionable.

### S2 — Priorización y remediación determinista
Señales comerciales integradas e impact score. Los cuatro tipos de operación. Ciclo
de lote completo con snapshot, escritura, verificación por delta y rollback.
Detección de regresión.

Este sub-proyecto entrega valor real **sin una sola llamada a IA**: corrección de
errores de escala, reconstrucción de nombres desde atributos existentes, reubicación
de códigos, normalización de unidades y opciones.

Incluye la **vía de configuración** (operación 5) con su aprobación de nivel
administrador y despliegue por etapas.

*Aceptación:* un lote aplicado y verificado en producción; rollback probado; una
regresión inducida a propósito es detectada y atribuida; la cola de trabajo ordenada
por impacto es la que el equipo de catálogo usa de verdad; un filtro perdido se activa
en una categoría, se verifica su efecto en la navegación, y se revierte limpiamente.

### S3 — Portero pre-registro
El motor de reglas expuesto como validación de lotes antes de la creación.
Pequeño, reutiliza todo S1. **Adelantable a antes de S2 si el sangrado por registro
rápido es urgente.**

*Aceptación:* el registrador recibe el veredicto antes de crear; un lote deliberadamente
malo es rechazado o advertido con el detalle de por qué.

### S4 — Capa IA
Extracción de atributos desde texto. Clasificación de categoría y attribute set.
Detección de incoherencias. Generación de nombre, descripción y short. Caché por
hash, triage, presupuesto por tenant.

*Aceptación:* precisión medida contra un conjunto etiquetado a mano por el equipo de
catálogo, con umbral acordado por tarea; coste de una pasada completa dentro del
presupuesto; ninguna propuesta de IA llega al catálogo sin aprobación.

### S5 — SEO y GEO
Ejes 9 y 10. Structured data, hreflang PY↔BR, duplicación entre store views,
consistencia schema ↔ texto, cobertura de preguntas de compra, cruce con búsqueda
interna.

*Aceptación:* hallazgos por store view; la incoherencia entre schema y texto visible
se detecta en casos reales; el informe de duplicación PY/BR es accionable.

### S6 — Multitenancy productiva y comercialización
Aislamiento endurecido, onboarding autoservicio, roles, presupuestos, borrado
verificable, informes exportables.

*Aceptación:* un segundo tenant se incorpora sin intervención de ingeniería.

---

## 12. Riesgos

| Riesgo | Mitigación |
|---|---|
| Las reglas inferidas aprenden el error sistemático como norma | Piso externo obligatorio; confianza y evidencia visibles; curación humana |
| El registrador pisa las correcciones y el sistema entra en bucle | Detección de regresión obligatoria; atribución por cohorte; portero pre-registro |
| Coste de IA fuera de control | Reglas primero, triage, caché por hash, modelo por tarea, presupuesto con parada dura |
| El write-back falla silenciosamente o escribe en el scope equivocado | Procedencia de scope en el espejo; resultado por ítem; verificación por delta; rollback |
| La API de Magento no aguanta el volumen | Módulo propio con lectura por cursor y proyección; deltas en lugar de re-scan |
| Custodiar catálogos ajenos | Aislamiento por tenant, cifrado, borrado verificable, auditoría inmutable |
| Nadie usa una lista de 40.000 hallazgos | Priorización por impacto desde S2; separación backlog de enriquecimiento vs deuda de calidad |
| Alcance: 10 ejes de golpe | Entrega por sub-proyecto, cada uno con valor propio |

---

## 13. Preguntas abiertas

1. **Versión y edición de Magento** (Open Source / Adobe Commerce), y si es on-prem o
   cloud. Condiciona el módulo y los límites de la API. *Pendiente.*
2. Presupuesto y tolerancia de coste de IA por mes.
3. Acceso a GA4 por tenant: ¿quién lo concede y cómo?
4. ¿Se instrumentará el registrador con sello de origen y flag de modo rápido?
5. Los problemas de catálogo aún no enumerados ("y otros"): cada uno de los aportados
   hasta ahora mejoró el diseño.
6. Confirmar idiomas y monedas exactos por store view (PY / BR).
7. Requisitos de residencia de datos de los 5 tenants objetivo.
8. **Nombre: decidido — Skudo.** Pendiente de verificación, no bloqueante para el
   diseño: disponibilidad de dominio, marca en DINAPI (Paraguay) e INPI (Brasil), y
   una lectura en voz alta por un hablante de portugués brasileño antes de imprimir
   nada.
9. **Benchmark entre tenants**: ¿se ofrece la comparación anonimizada de atributos y
   filtros entre catálogos como valor añadido? Aumenta mucho la calidad de las
   sugerencias del eje 11, pero exige adhesión explícita de cada tenant y garantías de
   agregación. Con 5 tenants, un agregado puede ser reidentificable: si se hace, el
   umbral mínimo de tenants por agregado debe definirse antes de construirlo.

---

## Fuentes de la investigación de mercado

- magendooro/magento2-catalog-quality — https://github.com/magendooro/magento2-catalog-quality
- Akeneo, mejores PIM 2026 — https://www.akeneo.com/blog/best-pim-2026/
- Salsify vs Akeneo — https://www.selecthub.com/pim-software/salsify-vs-akeneo/
- Comparativa Akeneo/Salsify/inriver/Sales Layer — https://blog.saleslayer.com/akeneo-salsify-inriver-sales-layer-pim-comparison
- Product content scoring — https://wisepim.com/ecommerce-dictionary/product-content-scoring
- Digital shelf analytics — https://theretailexec.com/tools/best-digital-shelf-analytics-software/
- Syndigo Product Content Analytics — https://syndigo.com/analytics/
- Mejores herramientas GEO para ecommerce — https://www.width.ai/post/the-best-generative-engine-optimization-tools-for-ecommerce
- GEO para catálogos grandes — https://www.nudgenow.com/blogs/generative-engine-optimization-guide-large-product-catalogs
- Amasty Mass Product Actions — https://amasty.com/mass-product-actions-for-magento-2.html
- Extensiones SEO Magento 2 — https://www.magedelight.com/blog/best-magento-2-seo-extensions-comparison/
- Auditoría Magento 2 técnica/SEO/AEO — https://angeo.dev/magento-2-audit/
