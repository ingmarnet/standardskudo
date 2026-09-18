# Stock y visibilidad en la nota — diseño

**Fecha:** 2026-09-18 · **Estado:** aprobado, pendiente de plan de implementación
**Contexto vivo:** [[skudo-stock-y-visibilidad]], [[standardskudo-project]],
[[skudo-servidor-produccion]], [[nissei-produccion-solo-lectura]]

## Problema

La nota de salud trata a todos los productos publicados por igual. Pero un producto
**sin stock** que Magento **oculta** (config `cataloginventory/options/show_out_of_stock = No`)
no es visible al cliente: penalizar sus defectos de calidad igual que los de un producto
en vitrina infla el ruido y baja la credibilidad de la nota. Un producto sin stock pero
que Magento **sí** muestra es un caso intermedio: se ve, pero comprarlo no se puede, así
que sus defectos importan menos que los de uno con stock.

Origen: el usuario observó que el SKU 1007, sin descripción y sin stock, no debería pesar
como un producto pleno «porque la regla en el config de Magento no muestra productos sin
stock» (2026-09-18).

## Principio rector aplicado

El mayor riesgo del proyecto es **confundir un dato desconocido con un veredicto**. Acá se
traduce en una regla dura: **un stock ausente NUNCA se interpreta como "sin stock".** Solo
un `is_in_stock = 0` explícito, combinado con una config `show_out_of_stock = No` también
explícita, saca a un producto de la nota. Si falta cualquiera de los dos datos, el producto
se evalúa como hoy. La ignorancia gana; nunca se fabrica un "sin stock" que oculte defectos
reales.

## Estado medido del terreno (2026-09-18)

- El **plumbing de stock ya existe end-to-end**: `SignalReader::getSignals()` llama a
  `attachInventory()` (línea 90), que lee `physical_qty` de `cataloginventory_stock_item.qty`
  y `salable_qty` de la tabla de índice MSI; la ingesta (`signal_sync.py`) los guarda en
  `product_signal` (`physical_qty`, `salable_qty`, ambas ya columnas del espejo).
- **No hay datos de stock sincronizados**: `product_signal` tiene 0 filas en el servidor,
  porque la ingesta necesita alcanzar el Magento y el server **no llega al 443** (pendiente
  de TI). El único rastro de stock en el espejo es el atributo `quantity_and_stock_status`,
  y es a medias: en Renovapadel store 1, 745 productos en `'1'` (en stock), 2936 en `None`,
  **cero en `'0'`**. No es confiable para decidir "sin stock".
- Lo que falta de dato: (1) `is_in_stock` (el flag con que Magento oculta), (2) la config
  `show_out_of_stock` por store view.

## El modelo: un cuarto factor del estado de vitrina

Hoy `Ficha.estado` (en `findings/catalog.py`) es `publicado / no_navegable / deshabilitado
/ desconocido`, derivado de `status` + `visibility`. Se extiende con stock + config a tres
niveles de PRIORIDAD sobre el subconjunto ya `publicado`:

| Condición (sobre un producto ya publicado y navegable) | Estado nuevo | Efecto en la nota |
|---|---|---|
| `is_in_stock = 1` | `visible_pleno` | Cuenta, prioridad plena |
| `is_in_stock = 0` **y** `show_out_of_stock = Sí` | `visible_sin_stock` | Cuenta, **prioridad menor** (orden) |
| `is_in_stock = 0` **y** `show_out_of_stock = No` | `oculto_sin_stock` | **No cuenta** (no_aplica), como no publicado |
| `is_in_stock` ausente **o** `show_out_of_stock` desconocido | (sin cambio) | Se evalúa como hoy |

Los estados previos (`no_navegable`, `deshabilitado`, `desconocido`) no cambian: siguen
siendo `no_aplica`/`desconocido` como hoy. El stock solo refina el subconjunto `publicado`.

**Decisión sobre la prioridad menor (resuelta):** un `visible_sin_stock` **cuenta igual en
la nota agregada**; su "menor prioridad" es de **ORDEN** en el panel (aparece después de los
`visible_pleno`, con un badge), no una ponderación distinta en el cálculo de la nota. Razón:
el efecto grande —sacar los ocultos de la nota— ya lo logra `oculto_sin_stock`; ponderar
además la nota agregada por stock la vuelve difícil de explicar sin ganar señal.

## Componentes y cambios

### 1. Módulo Magento (PHP) — solo lectura

Producción es **solo lectura** ([[nissei-produccion-solo-lectura]]): todo lo de acá son
lecturas nuevas, ningún write.

- **`is_in_stock`**: extender `SignalReader::attachInventory()` para traer también
  `cataloginventory_stock_item.is_in_stock` (no solo `qty`). `is_in_stock` es el flag que
  Magento consulta para ocultar; `qty` puede ser 0 con `is_in_stock = 1` (backorders), así
  que el flag es la verdad de "se puede comprar", no el número. Se añade a las filas que ya
  arma `attachInventory`, con la misma disciplina de NULL: sin fila de stock → `null`, nunca
  0.
- **`show_out_of_stock`**: leer `cataloginventory/options/show_out_of_stock` en **scope de
  store** (la config es por store view; puede variar por website). Exponerlo por store view.
  Sitio natural: `EnvironmentProbe` (ya expone entorno/edición) o una lectura de config de
  tienda dedicada. Decisión de plan: sumarlo al probe existente para no agregar un endpoint,
  salvo que el probe no tenga scope de store — en ese caso, un `/store-settings` mínimo.

### 2. Espejo (Python + migración)

- `product_signal.is_in_stock: Mapped[bool | None]` — nueva columna, `nullable=True`
  (null = no hay fila de stock / no se sincronizó; NUNCA se lee como "sin stock").
- **Config de tienda**: nueva tabla `store_setting` genérica —
  `(id, tenant_id, store_view_magento_id, key, value, synced_at)`, única por
  `(tenant_id, store_view_magento_id, key)`. Guarda `show_out_of_stock` como
  `"true"`/`"false"`, y queda lista para futuras configs de tienda sin otra migración.
  Alternativa descartada: una columna suelta en un modelo de store — no hay un modelo de
  store por tenant hoy, y una tabla clave/valor evita una migración por cada config nueva.

### 3. Ingesta

- `signal_sync.py`: mapear `is_in_stock` del envelope a la columna nueva.
- **Sync de config**: traer `show_out_of_stock` por store view y hacer upsert en
  `store_setting`. Puede vivir junto al sync del probe o en un `config_sync.py` chico. Es un
  dato por store view, no por producto: barato, una fila por store view.

### 4. Evaluación (`findings/catalog.py` y `findings/rules_eval.py`)

- `Ficha` gana lo que necesita para decidir el nivel: `is_in_stock: bool | None`, y el
  `muestra_sin_stock: bool | None` de la store view (se pasa al construir las fichas en
  `run.py`, que ya arma el contexto de la pasada; es un dato por store view, no por ficha,
  pero viaja en la ficha para que los detectores no cambien de firma).
- Nueva función de nivel de vitrina que devuelve `visible_pleno / visible_sin_stock /
  oculto_sin_stock` (o el estado previo `no_navegable`/etc.). `_publicado(f)` se redefine:
  un `oculto_sin_stock` **no** es publicado (→ `no_aplica` en todos los detectores y reglas,
  igual que hoy un no navegable). Un `visible_sin_stock` **sí** es publicado (se evalúa).
- El motor de reglas (`rules_eval.py`) usa el mismo `_publicado`, así que hereda el
  comportamiento sin cambios propios más allá de leer el nivel.

### 5. Scoring / prioridad (`score/`)

- La nota agregada no cambia de fórmula: los `oculto_sin_stock` ya no aportan hallazgos
  (salieron en la evaluación), y los `visible_sin_stock` aportan como cualquiera.
- El producto lleva un **flag de prioridad de vitrina** (derivado del nivel) que el panel
  usa para ordenar. No entra en el cálculo de la nota.

### 6. Panel (norma: cada avance se ve, [[skudo-avances-visibles-en-panel]])

- Vista **Productos**: badge de estado de vitrina (Con stock / Sin stock–visible / Oculto
  sin stock / Stock desconocido) y orden por prioridad (pleno → sin-stock-visible →
  el resto). Tooltip que explica cada uno.
- Vista **Hallazgos**: los de productos `oculto_sin_stock` ya no se generan; una nota breve
  explica que los productos ocultos por falta de stock no penalizan.
- Si no hay datos de stock aún (el caso de hoy, red bloqueada): el panel lo declara
  ("stock no sincronizado") en vez de mostrar todo como "con stock".

## Orden de construcción dado el bloqueo de red

El módulo PHP y la sincronización real dependen de alcanzar el Magento (443, pendiente de
TI). Por eso el plan separa:

- **Ahora, contra fixtures (sin red):** migración (`is_in_stock`, `store_setting`), el nivel
  de vitrina en `catalog.py`/`rules_eval.py`, el flag de prioridad en `score/`, y el panel.
  Todo testeable con fixtures que fijan `is_in_stock` y `show_out_of_stock`.
- **Cuando abra la red:** el `is_in_stock` en `SignalReader`, la lectura de
  `show_out_of_stock`, y los syncs. Hasta entonces, `is_in_stock` y `show_out_of_stock`
  quedan en null y —por el principio rector— nadie se oculta: la nota se comporta como hoy.

## Testing (medir antes que razonar, [[skudo-medir-antes-que-razonar]])

Tests de sabotaje e invariantes de objeto entero:

- Un `oculto_sin_stock` (is_in_stock=0 + show_out_of_stock=No) **no genera ningún hallazgo**
  y no baja la nota, aunque le falten description/precio/etc.
- Un `visible_sin_stock` (is_in_stock=0 + show_out_of_stock=Sí) **sí genera** sus hallazgos y
  cuenta en la nota, pero su flag de prioridad es menor.
- **Invariante de ignorancia:** con `is_in_stock` null O `show_out_of_stock` desconocido, el
  producto se comporta EXACTAMENTE como hoy (mismo conjunto de hallazgos que sin la feature).
  Un catálogo sin datos de stock produce la misma nota que antes del cambio.
- La cobertura declara los `oculto_sin_stock` como `no_aplica` con un motivo legible ("sin
  stock, oculto por configuración de la tienda"), no como un descuento silencioso.
- El orden del panel: `visible_pleno` antes que `visible_sin_stock`; empates estables.

## Fuera de alcance

- Ponderar la nota agregada por stock (se decidió que la prioridad es de orden).
- MSI fino / multi-almacén más allá de lo que `attachInventory` ya resuelve.
- Reaccionar a cambios de stock en tiempo real: la feature vive de la pasada de detección,
  no de un observador de inventario.
