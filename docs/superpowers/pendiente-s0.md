# S0 — trabajo pendiente y riesgos residuales

Estado al 2026-09-10 (tras el cierre de H3 y M7): 267 tests Python, 191 PHP (187 unitarias
+ 4 de integración), `ruff` limpio, cadena Alembic en `0013`.
Módulo Magento con ocho endpoints de lectura y un comando de consola, ingestor con CLI
(`python -m skudo.cli`) y espejo canónico en Postgres.

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

Lo que el cierre dejaba abierto, **cerrado en H3** (2026-09-10, ver
`docs/superpowers/h3-cli-y-escala.md`):

- La **reparación dirigida** de una partición divergente ya existe:
  `/checksums?partitions=ab,cd` publica los SKUs de esas particiones y
  `skudo repair` relee sólo esos.
- M7 (la cola crecía sin poda) también, con `bin/magento skudo:changelog:prune` y su
  cron.

---

## ~~H3~~ — CERRADO (2026-09-10) — `full_sync` no tenía punto de entrada ni sobrevivía a la escala

Cerrado en los commits `8f5dea3` (pasada reanudable), `a3062d2` (reparación dirigida),
`043c292` (CLI), `fdda4be` (poda de la cola, M7) y `eba6c63` (escritura por lote y test
de escala). El informe completo está en `docs/superpowers/h3-cli-y-escala.md`.

En cuatro partes:

1. `python -m skudo.cli` con once comandos (alta de tenant, sonda, pasada completa,
   delta, atributos, categorías, señales, reconciliación, reparación, estado y
   aceptación) y la convención de códigos de salida en `src/skudo/exit_codes.py`
   (0/1/2/3/64). El token nunca viaja por la línea de comandos ni se imprime: se lee
   de la variable que la FILA del tenant nombra.
2. Commit **por página** con punto de reanudación en `full_sync_checkpoint`
   (migración 0013), continuando la MISMA generación. El barrido exige el sello
   persistido `pass_complete` DENTRO de `_sweep`, así que un barrido sobre una pasada
   a medias no es expresable. Verificado matando el proceso con SIGKILL a mitad de una
   pasada real.
3. Reparación dirigida: `/checksums` acepta `partitions=` y devuelve los SKUs de esas
   particiones; `repair_partitions` los relee y borra del espejo lo que la partición ya
   no contiene.
4. Escala: la página se escribe en UNA sentencia multi-fila (617 → 2.900 productos/s;
   el coste dominante era compilar el SQL, no Postgres), test de escala de 10.000
   productos en la suite y una pasada de 228.889 SKUs × 2 store views medida a mano
   contra la instancia de desarrollo.

**Lo que queda declarado:** las dos store views se recorren en serie (paralelizar
necesita un candado por tenant que hoy no existe), no hay índice sobre
`sync_generation` (no fue medible frente al resto), y la medición de escala es sobre
datos sintéticos: cubre el orden de magnitud, no la forma real de los datos del
cliente.

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

**Con número, desde la pasada de escala de H3:** al barrer 457.762 `ProductRecord` que el
origen dejó de ofrecer, quedaron **457.762 filas huérfanas** en
`product_category_assignment`. El coste de este hallazgo crece con el catálogo entero, no
con la deriva.

---

## ~~M7~~ — CERRADO (2026-09-10) — `standard_skudo_change_log` crecía sin límite

Cerrado en el commit `fdda4be`. `bin/magento skudo:changelog:prune [--days=N]
[--dry-run]` y un cron diario borran una fila **sólo si** el ingestor ya la consumió
**y** además es más vieja que el margen de seguridad (configurable en
`standard_skudo/retention/change_log_days`, default 30, piso 1).

La condición de "ya consumida" no requiere que nadie informe nada: `/deltas?sinceId=X`
significa "dame los cambios posteriores a X", así que la petición ES la prueba de que
el consumidor aplicó todo hasta X, y `DeltaReader` guarda el máximo histórico en
`standard_skudo_delta_read` (`GREATEST`, para que un reintento no lo haga retroceder).
Sin ingestor que lea, el número no se mueve y la poda no borra nada.

**Lo que queda declarado:** el watermark de lectura es uno por instancia de Magento.
Dos consumidores con watermarks distintos sobre la misma instancia harían que gane el
mayor y el más atrasado podría perder filas; el margen de días es la segunda red. Ver
el docblock de `Model\DeltaReadWatermark`.

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

- ~~`sync_signals` existe pero no tiene punto de entrada de producción~~ — cerrado en
  H3: `python -m skudo.cli signals`, igual que el resto de las sincronizaciones.
- El chequeo AST de `tests/mirror/test_write_paths_are_reachable.py` empareja por nombre de
  método (`ast.Attribute.attr`), así que un método no relacionado que comparta un nombre
  `upsert_`/`set_` en otra parte de `src/` podría enmascarar un huérfano real. Es una
  sobreaproximación, no un defecto introducido.

---

## Nuevo tras H3 — el digest de `/checksums` sigue sin cota de memoria del lado PHP

`ChecksumReader` hace un `fetchAll` de `(sku, updated_at)` sobre el catálogo entero: en
la pasada grande de H3 funcionó con 228.889 filas y ~2 s por llamada, pero no hay una
prueba automatizada que afirme el coste y el array vive entero en memoria de PHP. Con
un catálogo tres veces mayor es el primer lugar donde este módulo se rompería. La
alternativa —recorrerlo por cursor y acumular sólo los 256 digests— es un cambio
contenido en esa clase.

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
