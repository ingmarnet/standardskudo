# S0 — trabajo pendiente y riesgos residuales

Estado al 2026-09-10: 185 tests Python, 105 PHP, `ruff` limpio, cadena Alembic en `0012`.
Módulo Magento con ocho endpoints de lectura, ingestor y espejo canónico en Postgres.

Este documento recoge lo que la revisión final de la fase de enmienda dejó **fuera** de la
ola de arreglos por requerir tareas nuevas y no parches. Está ordenado por lo que mordería
primero.

---

## H1 — La cola de cambios no ve las escrituras masivas, y nada aguas abajo puede detectarlo

**El único hallazgo que falla en silencio y de forma permanente.**

`Observer\ProductChanged` escucha `catalog_product_save_after` y `catalog_product_delete_after`.
Pero `Magento\Catalog\Model\Product\Action::updateAttributes()` —la acción masiva
"Actualizar atributos" del grid de admin, y la API que usan la mayoría de las herramientas
de terceros; esta tienda corre Amasty— despacha únicamente
`catalog_product_attribute_update_before` y **nunca** `save_after`. Verificado en
`vendor/magento/module-catalog/Model/Product/Action.php:85-100`. Lo mismo para
`updateWebsites()`. `CatalogImportExport` escribe por debajo del modelo entero.

Y la red de seguridad no lo tapa: `reconcile()` compara el **conjunto** de SKUs y su huella.
El propio docblock de `ChecksumReader` dice que detecta "un SKU borrado y otro creado". Un
**valor** cambiado en un SKU existente le es invisible, para siempre. Nada programa un full
sync periódico.

Dado que el spec §2 parte de que el registrador reescribe productos existentes y declara la
detección de regresión obligatoria, un espejo que puede sostener un valor rancio por tiempo
indefinido es el riesgo residual más grave.

**Trabajo:** observar también `catalog_product_attribute_update_before` y
`catalog_product_to_website_change`; y añadir reconciliación a nivel de contenido —hash por
partición de `updated_at`, o muestreo— o una cadencia de full sync obligatoria.

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
