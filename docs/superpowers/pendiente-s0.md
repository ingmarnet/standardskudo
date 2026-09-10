# S0 — trabajo pendiente y riesgos residuales

Estado al 2026-09-10 (tras el cierre de H1): 210 tests Python, 174 PHP (170 unitarias +
4 de integración), `ruff` limpio, cadena Alembic en `0012`.
Módulo Magento con ocho endpoints de lectura, ingestor y espejo canónico en Postgres.

Este documento recoge lo que la revisión final de la fase de enmienda dejó **fuera** de la
ola de arreglos por requerir tareas nuevas y no parches. Está ordenado por lo que mordería
primero.

---

## ~~H1~~ — CERRADO (2026-09-10) — La cola de cambios no veía las escrituras masivas

Cerrado en los commits `8752c6f`, `74a5f73` y `bafdad8`. El informe completo, con
el experimento que decidió la forma del arreglo, está en
`docs/superpowers/h1-deteccion-de-cambios.md`.

En dos mitades:

1. `Observer\ProductBulkChanged`, suscrito a `catalog_product_attribute_update_before`
   y `catalog_product_to_website_change`, con la traducción de `entity_id` a SKU en
   `Model\SkuResolver` (empareja por `entity_id`, nunca por la clave de la tabla) y
   `ChangeLog::recordMany()` para el volumen.
2. `/checksums` publica un digest de CONTENIDO por partición —256 particiones por
   hash del SKU, sobre el par `(sku, updated_at)`— y `reconcile()` lo recalcula sobre
   el espejo y reporta QUÉ particiones divergen. El criterio de aceptación 1 reprueba
   ahora también por contenido.

**El límite que queda declarado, con medición:** `catalog_product_entity.updated_at`
se mueve en el save del modelo, en `Action::updateAttributes()`, en una importación de
`CatalogImportExport` y en un `UPDATE` de SQL directo a la fila de entidad (por
`ON UPDATE CURRENT_TIMESTAMP`); **no** se mueve en `Action::updateWebsites()` —que por
eso necesita el observer de forma insustituible— ni en un `UPDATE` de SQL directo a
una tabla satélite (valores EAV, websites, categorías), que es el **piso** del
sistema: nada puede detectarlo.

Lo que el cierre deja abierto y le corresponde a otras tareas:

- La **reparación dirigida** de una partición divergente no existe: `reconcile()`
  detecta y nombra, y el remedio sigue siendo `full_sync`. Releer los SKUs de una
  partición es el bucle que `delta_sync` ya tiene sin factorizar, y darle un punto de
  entrada es H3.
- M7 (la cola crece sin poda) **empeora en magnitud**: una acción masiva sobre 20.000
  productos escribe ahora 20.000 filas donde antes escribía cero.

---

## H3 — `full_sync` no tiene punto de entrada, y no sobreviviría a la escala que exige el criterio

- **Sin entrada.** `main()` existe solo en `src/skudo/acceptance/s0.py`. No hay
  `python -m skudo.ingest.full_sync`, ni camino de alta de tenants. El comando de aceptación
  presupone un espejo poblado que nada permite poblar.
- **Una transacción para el catálogo entero.** `full_sync` hace `session.commit()` una vez,
  al final. Para el piloto son 456.974 upserts más ~228k pares delete+insert de categorías en
  una sola transacción de Postgres, sin lotes, sin commit por página y sin reanudación. Un
  fallo a la tercera hora lo pierde todo. `sync_generation` da un sello reanudable y nadie lo
  usa así.
- Nada afirma la escala en ningún test: `_espejo_sincronizado` pasa con un espejo de dos SKUs.

El criterio de aceptación 1 dice "espejo de 200k SKUs × 2 store views sincronizado". Tal como
está, el código no puede intentarlo.

**Trabajo:** CLI de ingesta con alta de tenant; commit por página con watermark reanudable;
un test de escala aunque sea con datos sintéticos.

---

## M5 — La mitad más importante del payload de `/products` no la ejecuta ningún test PHP

`FakeSelect::join()` en `ProductReaderTest` es un no-op que devuelve `$this`, y
`evaluateEntitySelect()` devuelve `[]` para cualquier tabla que no sea
`catalog_product_entity`. Así que `eavValues()`, `websiteIds()` y `categoryIds()` no se
ejecutan nunca. Nada afirma `global_values`, `store_values`, `website_ids`, `category_ids`,
`mpn`, `model`, `gtin` ni `variant_key`.

`websiteIds()` es la **única** condición que discrimina PY de BR en este tenant, porque ambas
store views cuelgan de la root category 2. Es load-bearing y no está probada ni en la
semántica del join ni en la forma emitida.

---

## M3 — Filas huérfanas en tres tablas, sin barrido

`delta_sync` borra el `ProductRecord` de un SKU eliminado pero nunca sus filas de
`ProductCategoryAssignment`; el `_sweep` de `full_sync` borra solo `ProductRecord`.
`sync_categories` y `sync_attributes` documentan "no hay barrido" como fuera de alcance.

Consecuencia para S1: el `option_id` de una opción borrada sigue pareciendo vivo en
`attribute_option`, y existen asignaciones para SKUs sin registro de producto. El caso de las
opciones importa porque la consolidación de S1 decide por `option_id`.

---

## M7 — `standard_skudo_change_log` crece sin límite en la base del cliente

Insert-only por diseño, sin poda ni retención. Una sola importación masiva escribe 228k filas.
No hay comando de limpieza ni cron.

Agravado por el cierre de H1: los observers de escritura masiva escriben una fila por
producto de la selección, así que una acción del grid sobre 20.000 productos deja 20.000
filas donde antes dejaba cero. El arreglo de H1 es correcto —esas filas son la única
señal de esas escrituras— y hace que la poda pase de deseable a necesaria.

---

## M8 — `attachSearchDemand` es O(consultas × SKUs)

2.000 consultas × 6.986 SKUs con ventas (store 1, 90 días) ≈ 14M `str_contains` por llamada.
Inofensivo aquí solo porque la instancia tiene dos filas de búsqueda; no lo sería en un tenant
con estadísticas reales.

---

## L5 — El espejo no puede computar la "accesibilidad efectiva" del eje 2

El spec §5.6 exige que `categories` lleve flags de anchor y clase de categoría. Ninguno está
modelado, y `derive_category_effect` ignora anchor.

---

## Observaciones menores de la última re-revisión

- `sync_signals` existe pero no tiene punto de entrada de producción — parte de H3.
- El chequeo AST de `tests/mirror/test_write_paths_are_reachable.py` empareja por nombre de
  método (`ast.Attribute.attr`), así que un método no relacionado que comparta un nombre
  `upsert_`/`set_` en otra parte de `src/` podría enmascarar un huérfano real. Es una
  sobreaproximación, no un defecto introducido.

---

## Lo que no se puede verificar sin desplegar el módulo

Recogido en `docs/superpowers/plans/s0-verificacion-manual.md`, que es la única cobertura de
estos puntos:

1. La forma de las respuestas sobre HTTP real. El envoltorio de B1 se prueba contra el
   `ServiceOutputProcessor` real, lo cual es lo más cerca que se llega sin desplegar, pero
   nadie ha hecho todavía una petición de verdad.
2. La latencia de delta, que ningún criterio del arnés mide.
3. El acuerdo del digest entre el `sorted()` de Python y el `sort($skus, SORT_STRING)` de PHP
   a escala de 228k SKUs.
4. La corrección de `eavValues()`, `websiteIds()` y `categoryIds()` contra datos reales (M5).
