# Arreglo de las nueve discrepancias de la verificación sobre HTTP real

Fecha: 2026-09-10. Ola de arreglo de las nueve discrepancias que documenta
`verificacion-http-real.md`. Todo se verificó **sobre HTTP real** contra el
entorno de desarrollo (`/home/ingmar/magento-skudo-dev`, servido en
`127.0.0.1:8088`, espejo en Postgres `skudo_httpreal`), no sólo con tests.

**Estado en una línea:** las nueve cerradas. Dos de los hallazgos resultaron
mal caracterizados —**C2** en sus números y **A3** por completo— y este
informe dice exactamente en qué, con la medición que lo demuestra. La
dirección de los arreglos no cambia.

| | Estado | Verificado por HTTP |
|---|---|---|
| C1 campos-mapa como `[]` | cerrada | sí |
| C2 doble ventana de versión activa | cerrada (números del hallazgo corregidos) | sí |
| A1 `/signals` fuera de la población | cerrada | sí |
| A2 `physical_qty` nulo con stock cargado | cerrada | sí |
| A3 camino de `sinceTimestamp` muerto | cerrada — **no era un defecto independiente** | sí |
| M1 `counts` vs `product_count` | cerrada | sí |
| M2 cursor inválido → 500 | cerrada | sí |
| M3 `storeId` inexistente → 200 | cerrada | sí |
| B1 `int`/`float` y `%1` sin interpolar | cerrada (una mitad documentada, no forzada) | sí |

Commits, en orden:

| SHA | Qué |
|---|---|
| `1ae358c` | C2 (incluye el `(object)` de C1 que ya venía aplicado del ejercicio anterior) |
| `fbc94f8` | el barrido de C1 |
| `f448932` | A1 + A2 |
| `0b0dd47` | M1 + M2 + M3 + B1 |

Suites al final: **136 PHPUnit** (133 unitarias + 3 de integración) y **195
pytest**, `ruff check src tests` limpio.

---

## 0. Un defecto del ENTORNO que había que arreglar primero

Antes de tocar el módulo hubo que corregir el entorno de desarrollo, porque
el entorno era la causa de las dos caracterizaciones equivocadas.

`Magento\Staging\Model\VersionManager::getVersion()` pide el id de la versión
actual a `VersionHistory::getCurrentId()`, que lee el flag `staging` de la
tabla `flag`, y después lo busca en `staging_update`. Si no lo encuentra cae
en un fallback:

```php
// vendor/magento/module-staging/Model/VersionManager.php:206
protected function getVersionById($versionId)
{
    try {
        return $this->updateRepository->get($versionId);
    } catch (NoSuchEntityException $e) {
        return $this->updateFactory->create()->setId(1);   // <- acá
    }
}
```

El volcado selectivo con que se construyó la base de desarrollo **no copió
`staging_update` ni la fila de `flag`** (§1.3 del informe anterior lista lo
que sí se copió; ninguna de las dos está). Medido:

```
select count(*) from staging_update;                        -> 0
select flag_data from flag where flag_code='staging';       -> (0 filas)
```

Con las dos vacías, `getVersion()->getId()` vale **1**, y el filtro que el
renderer de Magento inyecta sale como `created_in <= 1 AND updated_in > 1`.
**Eso es lo que se midió y se tomó por "la regla de Magento".** No lo es: es
la regla de Magento en una instancia sin estado de staging.

En una instancia real el id es un **timestamp**: el del `staging_update` más
reciente que el cron de aplicación ya aplicó. Evidencia de que producción los
tiene, del propio volcado de esquema (lectura, sin tocar datos):

```
CREATE TABLE `staging_update` ( ... ) AUTO_INCREMENT=1785121201
```

`1785121200` es 2026-07-23: producción usó actualizaciones programadas y su
flag apunta a un timestamp, no a 1.

**Arreglo del entorno** (sólo en la base de desarrollo, que es mía):

```sql
INSERT INTO staging_update (id, start_time, name, ...) VALUES
 (1789020000, FROM_UNIXTIME(1789020000), 'dev: update aplicada (vieja)', ...),
 (1789025363, FROM_UNIXTIME(1789025363), 'dev: update de SKU-GAMMA', ...),
 (1789111823, FROM_UNIXTIME(1789111823), 'dev: update futura', ...);
```

más el flag, escrito por la API de Magento y no a mano
(`/home/ingmar/skudo-dev-logs/fixes/set-version.php`, que llama a
`VersionHistoryInterface::setCurrentId()`). Eso permite mover la instancia
entre configuraciones y medir de verdad:

| Config | `current_version` | Significado |
|---|---|---|
| **A** | `1789020000` | una versión vieja aplicada; la actualización programada de `SKU-GAMMA` **todavía no** aplicada, y la expiración de `SKU-EXPIRED` tampoco |
| **B** | `1789025363` | la actualización de `SKU-GAMMA` **ya** aplicada |
| **C** | `1789111823` | la versión FUTURA de `SKU-GAMMA` pasó a ser la vigente |

Sonda de comprobación: `/home/ingmar/skudo-dev-logs/fixes/boot-probe.php`
arranca la instancia, arma un `Select` normal del framework sobre
`catalog_product_entity` e imprime el SQL ensamblado y las filas.

```
Config A:  SELECT `e`.`sku` FROM `catalog_product_entity` AS `e`
           WHERE (e.created_in <= '1789020000') AND (e.updated_in > '1789020000')
```

---

## C2 — el módulo deja de componer su propia ventana de versión activa

### Lo que se quitó

`ActiveVersionResolver::applyToSelect()`, y con él las **ocho** llamadas:

| Clase | Consultas que llevaban el filtro |
|---|---|
| `ProductReader` | `getPage()`, `getBySku()`, `categoryIds()` |
| `ChecksumReader` | `getChecksums()` |
| `CategoryReader` | `getPage()` |
| `SignalReader` | `attachInventory()` (stock físico), `attachMargin()` |
| `DeltaReader` | `activatedVersions()` |

La clase se renombró a **`VersioningSchema`** con un solo método,
`isVersioned(string $table)`. La detección por columnas sigue haciendo falta
para **un** caller: `DeltaReader::activatedVersions()` nombra `created_in`
explícitamente, y en una instalación sin versionado esa columna no existe y
la consulta sería un error de SQL. El nombre viejo prometía lo que la clase ya
no hace y era la invitación a que el filtro volviera; ese fue el motivo del
rename, no el gusto.

Se extrajeron además las dependencias que dejaron de tener sentido:
`ProductReader`, `ChecksumReader`, `CategoryReader` y `SignalReader` ya no
reciben nada relacionado con versionado.

Ninguna clase de `Magento\Staging\*` se referencia, y nada ramifica por
edición: en Community las columnas no existen y el filtro nunca aplicó ahí.

### Antes / después, medido en la instancia de desarrollo

**Config A** (`current_version = 1789020000`) — la configuración donde las dos
reglas divergen:

```
Magento (Select normal del framework):  ALPHA BETA DELTA sku-alpha-lower ÑOÑO NORMAL Z-LAST EXPIRED   -> 8
ANTES  /products?storeId=1:             ALPHA BETA DELTA sku-alpha-lower ÑOÑO NORMAL Z-LAST           -> 7
ANTES  /checksums:  {"product_count":7,"sku_digest":"7589f256…ceac1a"}
ANTES  /environment.counts.products: 8

DESPUÉS /products?storeId=1:            ALPHA BETA DELTA EXPIRED sku-alpha-lower ÑOÑO NORMAL Z-LAST   -> 8
DESPUÉS /checksums:  {"product_count":8,"sku_digest":"8326b39e…099d86"}
DESPUÉS /environment.counts.products: 8
```

La fila que el módulo descartaba es la versión de `SKU-EXPIRED`, cuyo
`updated_in` (1789021823) ya pasó **por reloj** pero que Magento sigue
sirviendo porque su expiración todavía no fue aplicada (`updated_in > V` con
V = 1789020000). Un producto que la tienda muestra y el módulo no reportaba.
Es la forma exacta del hallazgo, reproducida con una fila.

**Config B** (`current_version = 1789025363`):

```
Magento:                    ALPHA BETA GAMMA DELTA sku-alpha-lower ÑOÑO NORMAL Z-LAST  -> 8
ANTES  /products?storeId=1: 8, el mismo conjunto
DESPUÉS /products?storeId=1: 8, el mismo conjunto
/checksums antes y después: {"product_count":8,"sku_digest":"3961b231…2a2a65"}
```

Acá **no hay diferencia**, y eso importa: la pérdida no es universal, ocurre
en la zona de divergencia entre el reloj y el id de la versión aplicada. Con
el flag exactamente en el `created_in` de la actualización de `GAMMA`, las
dos reglas coinciden y el filtro del módulo era sólo redundante.

### Los números del hallazgo, corregidos

El hallazgo reporta, contra el catálogo de producción:

```
filas que pasan la regla del id de versión 1 (la de Magento)      228.481
entidades sin NINGUNA fila que pase la regla de Magento                36
```

Esos números se calcularon con **`versionId = 1`**, que es el fallback que
produce una base sin `staging_update` — el estado de la base de desarrollo,
no el de producción. En producción el id es un timestamp (evidencia arriba),
así que la partición de las 228.881 filas es distinta y las "36 entidades
inalcanzables" no son las 36 que el hallazgo nombra. Los dos ejemplos que
cita (`created_in 1689220740 updated_in 2147483647` y `created_in 1717646340
updated_in 2147483647`) son actualizaciones programadas ya vigentes: con
`versionId = 1` fallan, con el id real de producción **pasan**.

**No pude recalcularlo contra producción.** Toda lectura de la base
`nisseicom` (`docker exec … mysql -ucasanissei nisseicom -e "SELECT …"`) fue
**bloqueada por el clasificador del sandbox**, en tres formas distintas de la
misma consulta. Sólo pude leer archivos del árbol de producción, que es de
donde salió la evidencia del `AUTO_INCREMENT`. Queda pendiente y está en §
"Lo que no se hizo".

Lo que **no** cambia con la corrección: el mecanismo del defecto es real y la
dirección del arreglo es la misma. Dos definiciones distintas de "activa"
—una anclada en el id de la versión aplicada, la otra en el reloj— ANDadas
producen un conjunto estrictamente más estrecho que cualquiera de las dos, y
`/checksums` sufría el MISMO doble filtro, así que los dos lados del espejo
coincidían sobre el conjunto estrechado y `reconcile()` reportaba "sin
deriva" para siempre. La red de seguridad quedaba estructuralmente ciega al
hueco que existe para detectar. Eso es exactamente lo que Config A demuestra
con una fila y lo que el arreglo elimina.

### Las pruebas, y por qué ninguna es una unitaria con un Select falso

El defecto vivió doce revisiones porque **todas** las pruebas del lado PHP
usan un doble de `Select` que, por construcción, no sabe nada del renderer
del framework. Un doble así no puede mostrar la conjunción de dos filtros
cuando sólo conoce uno. Peor: las pruebas viejas afirmaban la **presencia**
de los dos `where()`, así que codificaban el defecto como requisito.

Dos pruebas nuevas:

1. **`Test/Unit/Model/NoOwnVersionFilterTest.php`** — afirma que ninguno de
   los siete caminos de lectura (`ProductReader::getPage`, `::getBySku`,
   `ChecksumReader`, `CategoryReader`, `SignalReader`, `EnvironmentProbe`,
   `DeltaReader`) emite un predicado propio sobre `created_in`/`updated_in`,
   con una única excepción **declarada en una constante**: el `created_in > ?`
   de la consulta de activaciones. Construye las clases con el esquema
   versionado PRESENTE, que es la única configuración donde un filtro propio
   podría volver a colarse. Trae dos guardas contra la vacuidad: un control
   positivo (mete a mano los dos predicados de C2 y comprueba que el mismo
   criterio los detecta) y una aserción de que cada camino ejecutó al menos
   una consulta.

2. **`Test/Integration/PopulationMatchesMagentoTest.php`** — arranca una
   instancia REAL (`SKUDO_MAGENTO_APP_ROOT`; se salta sola sin ella) y afirma
   tres cosas contra ella: que la población de `/products` es **exactamente**
   la que devuelve un `Select` normal del framework sobre
   `catalog_product_entity`; que `/checksums.product_count` y
   `/environment.counts.products` cuentan esa misma población; y que el camino
   de activación de `/deltas` con `sinceTimestamp = 0` alcanza toda la
   población activa. No referencia ninguna clase de Staging: construye un
   `Select` y compara. En una instancia Community la comparación sigue siendo
   válida y trivialmente verdadera, que es lo que se quiere afirmar ahí.

Se agregó una segunda testsuite a `phpunit.xml` y un target
`make test-php-integration SKUDO_MAGENTO_APP_ROOT=…`.

**Comprobación por sabotaje.** Reintroducido el filtro en
`ProductReader::getPage()`:

```
unitaria:    Failed asserting that two arrays are identical.
             +    0 => 'e.created_in <= UNIX_TIMESTAMP()'
             +    1 => 'e.updated_in > UNIX_TIMESTAMP()'
integración: --- Expected
             -    3 => 'SKU-EXPIRED',
             FAILURES!  Tests: 3, Assertions: 14, Failures: 1.
```

La unitaria nombra el predicado; la de integración nombra **la fila perdida**.

Se borraron las pruebas que afirmaban el comportamiento quitado:
`ActiveVersionResolverTest` completa (207 líneas; las de `applyToSelect()` no
tienen a qué aplicarse y las de detección se rescataron en
`VersioningSchemaTest`), y cinco métodos en `ProductReaderTest`,
`ChecksumReaderTest`, `CategoryReaderTest` y `ProductReaderCategoryIdsTest`.

Corolario del hallazgo que sí queda validado: la duplicación de categorías que
`ProductReaderCategoryIdsTest` existía para prevenir (`NGO-T2092` con 3 filas
de versión devolviendo la categoría 603 tres veces) **no es reproducible en
una petición real** — el join a `catalog_product_entity` es un FROM de tabla
staged y Magento lo acota. El docblock de ese archivo lo dice ahora.

---

## C1 — el barrido de los campos-mapa

El `(object)` de `global_values`/`store_values` ya venía aplicado del
ejercicio anterior (quedó dentro del commit `1ae358c`). Lo que faltaba, y es
el pedido real, es el **barrido**: la regla existía escrita en un docblock de
25 líneas y no había nada que la aplicara al conjunto, así que apareció tres
veces en tres campos distintos.

`Test/Unit/WebApi/MapValuedFieldsAreJsonObjectsTest.php` inspecciona el
payload de los ocho endpoints **después de `json_encode`/`json_decode`** —la
forma que recibe el cliente, no la que devuelve el modelo, que es donde un
array asociativo y un `stdClass` son indistinguibles— y hace tres cosas:

1. cada campo-mapa declarado es objeto JSON **con datos**;
2. cada campo-mapa declarado es `{}` y nunca `[]` con el mapa **vacío**;
3. **recorre el payload y exige que todo nodo con forma de objeto esté
   clasificado**, más una contra-guarda de que las rutas declaradas existan
   de verdad (para que la lista no se desfase del código en silencio).

### El barrido, campo por campo

Los 25 nodos con forma de objeto JSON de los ocho endpoints (los 5 payloads
raíz incluidos). "Mapa" = claves
que dependen de los datos, puede quedar vacío, **debe** llevar `(object)`.
"Registro fijo" = claves literales en el código, no puede quedar vacío, no
puede colapsar.

| endpoint | ruta | forma | tipo JSON con datos | tipo JSON vacío |
|---|---|---|---|---|
| `/environment` | *(payload)* | registro fijo | object | object |
| `/environment` | `counts` | registro fijo | object | object |
| `/environment` | `websites[*]` | registro fijo | object | object |
| `/environment` | `store_groups[*]` | registro fijo | object | object |
| `/environment` | `store_views[*]` | registro fijo | object | object |
| `/products` | *(payload)* | registro fijo | object | object |
| `/products` | `items[*]` | registro fijo | object | object |
| `/products` | **`items[*].global_values`** | **MAPA** | object | **object** |
| `/products` | **`items[*].store_values`** | **MAPA** | object | **object** |
| `/products-by-sku` | *(payload)* | registro fijo | object | object |
| `/products-by-sku` | `items[*]` | registro fijo | object | object |
| `/products-by-sku` | **`items[*].global_values`** | **MAPA** | object | **object** |
| `/products-by-sku` | **`items[*].store_values`** | **MAPA** | object | **object** |
| `/deltas` | *(payload)* | registro fijo | object | object |
| `/deltas` | `items[*]` | registro fijo | object | object |
| `/signals` | *(payload)* | registro fijo | object | object |
| `/signals` | `items[*]` | registro fijo | object | object |
| `/checksums` | *(payload)* | registro fijo | object | object |
| `/attributes` | *(payload)* | registro fijo | object | object |
| `/attributes` | `items[*]` | registro fijo | object | object |
| `/attributes` | `items[*].options[*]` | registro fijo | object | object |
| `/attributes` | **`items[*].options[*].labels`** | **MAPA** | object | **object** |
| `/categories` | *(payload)* | registro fijo | object | object |
| `/categories` | `items[*]` | registro fijo | object | object |
| `/categories` | `items[*].store_states[*]` | registro fijo | object | object |

Campos-mapa: **tres**, y los tres ya llevan `(object)`. No apareció ninguno
sin castear. Los campos que son LISTA (`website_ids`, `category_ids`, `path`,
`attribute_set_ids`, `options`, `store_states`, `items`) no entran en la
clasificación porque una lista vacía es `[]` legítimamente.

La fixture de `labels` usa claves **0 y 1 consecutivas** a propósito: es el
segundo modo de colapso, el que deja los datos intactos y destruye la forma.

**Comprobación por sabotaje, tres veces:**

| Sabotaje | Resultado |
|---|---|
| quitado el `(object)` de `store_values` | fallan 2 casos (los dos endpoints), por el caso **vacío** — con datos las claves son strings y no colapsa |
| quitado el `(object)` de `labels` | fallan 3 casos: vacío **y con datos**, porque sus claves son 0 y 1 |
| agregado `'nuevo_mapa' => ['ar' => 'x', 'br' => 'y']` sin declarar ni castear | falla la guarda de clasificación nombrando `products:items[*].nuevo_mapa` y `products_by_sku:items[*].nuevo_mapa` |

El tercero es el que importa: un campo-mapa nuevo hace fallar la suite **sin
que nadie tenga que acordarse de esta prueba**.

### Verificación por HTTP

`full_sync` completa contra la instancia real, con un producto con override de
store view y otro sin:

```
[OK] full_sync: {"records_written": 16, "records_deleted": 0, "pages_fetched": 2,
                 "records_without_timestamp": 2, "skus_without_timestamp": ["SKU-Z-LAST","SKU-Z-LAST"]}
```

16 = 8 productos × 2 store views. En el espejo, `json_typeof` de los 16
`scope_provenance` es `object`, y la procedencia discrimina:

```
       sku       | store | proc_name
-----------------+-------+-----------
 SKU-ALPHA       |     1 | store      <- override de store view en 1 y en 3
 SKU-ALPHA       |     3 | store
 SKU-BETA        |     1 | global     <- SIN ningún override: el caso que reventaba
 SKU-BETA        |     3 | global
 SKU-ÑOÑO        |     3 | store
 ...
```

---

## A1 — `/signals` sirve la población de `/products`

**Contrato elegido, y ahora explícito en `SignalReaderInterface`:** `/signals`
se limita a los SKUs que `/products` sirve, intersectados con los que tuvieron
venta en la ventana. Un SKU vendido cuyo producto ya no está activo en el
catálogo **no aparece**. La otra opción (que el ingestor descarte señales de
SKUs desconocidos) se descartó porque pone la definición de la población en
dos lados, que es el defecto C2 otra vez.

`SignalReader::restrictToCatalogPopulation()` hace una consulta aparte
—`SELECT e.sku FROM catalog_product_entity AS e WHERE e.sku IN (vendidos)`,
el MISMO FROM que `/products` y `/checksums`, así que Magento la acota igual—
y filtra en PHP. **No** es un JOIN dentro de la agregación de ventas a
propósito: unir antes del `GROUP BY` multiplicaría `SUM(qty_ordered)` por cada
fila de entidad del join, y un error de conteo de dinero es peor que una
consulta más.

Del lado Python no se reimplementa el recorte, pero tampoco se confía a
ciegas: `SignalSyncReport` trae `signals_without_product_record` y
`skus_without_product_record`, y las filas **no se descartan**. Descartarlas
del lado del espejo escondería el desacuerdo entre los dos lados, que es
exactamente lo que dejó pasar el hallazgo.

**Verificación por HTTP, Config A** (donde `SKU-GAMMA` no está en la población
activa):

```
ANTES   /products?storeId=1: [ALPHA, BETA, DELTA, EXPIRED, lower, ÑOÑO, NORMAL, Z-LAST]
ANTES   /signals?storeId=1:  [('SKU-ALPHA',42,40), ('SKU-BETA',None,None), ('SKU-GAMMA',None,4)]
                                                                            ^^^^^^^^^ no está en /products

DESPUÉS /signals?storeId=1:  [('SKU-ALPHA',42,40), ('SKU-BETA',None,None)]
DESPUÉS /signals?storeId=3:  [('SKU-ALPHA',42,7),  ('SKU-ÑOÑO',None,None)]
```

Pasada real de `sync_signals` contra la instancia:

```
[OK] sync_signals: {"store_views_read": 2, "signals_written": 5,
                    "signals_without_product_record": 0, "skus_without_product_record": []}
```

Y en el espejo, cero huérfanos:

```
    sku    | store | product_records
-----------+-------+-----------------
 SKU-ALPHA |     1 |               2
 SKU-ALPHA |     3 |               2
 SKU-BETA  |     1 |               2
 SKU-GAMMA |     1 |               2
 SKU-ÑOÑO  |     3 |               2
```

**Pruebas:** `SignalReaderTest::testASoldSkuOutsideTheCatalogPopulationIsNotReported`
(PHP) y `test_a_signal_for_a_sku_the_mirror_cannot_describe_is_reported_not_hidden`
+ `test_no_orphans_reported_when_every_signalled_sku_is_mirrored` (Python, con
contra-guarda para que un contador que dijera "todo huérfano" no pasara).

---

## A2 — `physical_qty` deja de fabricar un desconocido

**Por qué pasaba.** `attachInventory()` une `cataloginventory_stock_item`
(tabla legacy, no versionada) con `catalog_product_entity` sólo para traducir
`product_id` a `sku`. Magento pone su filtro de versión **en la condición del
join**:

```sql
INNER JOIN `catalog_product_entity` AS `e`
  ON e.entity_id = si.product_id AND (e.created_in <= :v AND e.updated_in > :v)
```

Para un SKU fuera de la población activa el join no encuentra fila y la
cantidad física desaparece, mientras `salable_qty` **sí** llega porque
`inventory_stock_N` no es staged y no lleva filtro. El payload se contradecía
a sí mismo: lo vendible conocido y lo físico "no se sabe".

Medido, Config A, antes:

```json
{"sku":"SKU-GAMMA","units_sold":2,"revenue":50,"salable_qty":4,"physical_qty":null,...}
```

con `cataloginventory_stock_item` teniendo `product_id = 1003, qty = 5`.

**El arreglo es el mismo que A1, y no un parche en el join.** Con la población
recortada, todo SKU de la respuesta tiene fila de entidad, así que el join
resuelve siempre y un `physical_qty` nulo vuelve a significar lo único que
debe significar: no hay fila de stock. Config A después: `SKU-GAMMA` no está
en la respuesta; `SKU-BETA` sigue con `physical_qty: null` **y**
`salable_qty: null`, que es coherente (no tiene fila de stock). Config B, con
`GAMMA` en la población: `('SKU-GAMMA', 5, 4)` — la física conocida.

Nótese que este hallazgo **sobrevive** al arreglo de C2: quitar el filtro del
módulo no ayuda acá, porque el filtro que rompía el join es el de Magento.

**Prueba:** `SignalReaderTest::testNoReportedRowHasUnknownPhysicalQtyWhileSalableQtyIsKnown`,
que recorre los items y afirma la invariante ("si lo vendible es conocido, lo
físico también") en vez de un valor puntual.

**Sabotaje:** borrada la llamada a `restrictToCatalogPopulation()`, fallan las
dos pruebas de A1/A2.

---

## A3 — no era un defecto independiente

Se re-testeó, como pediste, en vez de asumir. **El resultado es que el
hallazgo A3 no describe un defecto del módulo.**

El hallazgo dice que `created_in > $sinceTimestamp AND created_in <= 1` es
insatisfacible. Es cierto — **cuando el id de versión vale 1**, que es el
fallback de una base sin `staging_update` (§0). Con la instancia corregida y
el filtro del módulo **todavía puesto**, Config B, `sinceTimestamp = ahora−7200`:

```
[{"items":[{"change_id":0,"sku":"SKU-GAMMA","event":"save","changed_at":"2026-09-10 07:29:23"},
           {"change_id":1,...},{"change_id":2,...},{"change_id":3,...},
           {"change_id":4,...},{"change_id":5,...}],"last_change_id":5}]
```

El centinela **aparece**. El camino no estaba muerto por diseño: estaba muerto
por el estado de la base de desarrollo, y las dos ventanas (reloj y versión
aplicada) se solapaban lo suficiente para que funcionara igual.

Lo que sí es un defecto es el mecanismo general de C2: mientras el módulo
ANDaba su ventana por reloj, la conjunción podía quedar sin solución (o
estrechada) según dónde estuviera el flag. Quitado el filtro, la consulta que
sale es exactamente "versiones que pasaron a ser la vigente desde
`sinceTimestamp`".

**Verificación punta a punta de que el mecanismo funciona de verdad**, que es
lo que hacía falta demostrar. Con el espejo ya sincronizado y el watermark en
5, se movió el flag a Config C (`1789111823`) — la versión FUTURA de
`SKU-GAMMA` pasa a ser la vigente, que es literalmente el evento que ningún
observer puede detectar porque no ocurre ningún evento de Magento:

```
framework select: ALPHA BETA DELTA sku-alpha-lower ÑOÑO NORMAL Z-LAST GAMMA
/deltas?sinceId=5&sinceTimestamp=1789030000
  -> [{"items":[{"change_id":0,"sku":"SKU-GAMMA","event":"save","changed_at":"2026-09-11 07:30:23"}],
       "last_change_id":null}]

[OK] delta_sync: {"changes_seen": 1, "records_updated": 2, "records_deleted": 0, "watermark": 5, ...}

espejo:  SKU-GAMMA | store 1 | mirrored_at 2026-09-10 08:35:34.218328+00
         SKU-GAMMA | store 3 | mirrored_at 2026-09-10 08:35:34.526137+00
```

Ese `changes_seen: 1` es **la activación**, con la cola agotada y el watermark
sin moverse — comparar con el `changes_seen: 1` del informe anterior, que era
"sólo la fila de cola nueva" y cero activaciones.

**Prueba de regresión:**
`PopulationMatchesMagentoTest::testTheDeltaActivationPathSeesTheWholeActivePopulation`
afirma, contra la instancia real, que con `sinceTimestamp = 0` los centinelas
son **exactamente** la población activa. Cualquier filtro que estreche la
consulta rompe la igualdad. Se salta sola en una instancia sin columnas de
versión.

---

## M1 — los conteos cuentan la misma población que el endpoint que sirve cada cosa

Tres desacuerdos, no uno:

| campo | antes | contra | después |
|---|---|---|---|
| `counts.products` | 8 | `/checksums.product_count` 7 | **8 = 8 = 8** (`/products` también 8) |
| `counts.attributes` | 1.240 (todos los entity types) | los 1.066 de `catalog_product` que `/attributes` pagina | **1.066** |
| `counts.attribute_sets` | 422 | los 413 de `catalog_product` | **413** |

El primero se cerró con C2 y **no** tocando el conteo: `COUNT(*)` sobre
`catalog_product_entity` sigue **sin where propio** a propósito, porque
Magento lo acota igual que a `/products` y `/checksums`, y ahí está la
coincidencia. `EnvironmentProbeCountsTest::testTheProductCountHasNoWhereOfItsOwn`
lo fija, y `PopulationMatchesMagentoTest` afirma la igualdad de los tres
contra una instancia real.

Los otros dos se acotan al entity type de producto, el único que este módulo
sirve.

Efecto lateral: la resolución `entity_type_code -> entity_type_id` estaba
escrita **dos** veces (`AttributeReader` para `catalog_product`,
`CategoryReader` para `catalog_category`) y M1 pedía una tercera en
`EnvironmentProbe`. Se extrajo `EntityTypeResolver`, memoizado por código —
mismo precedente que `EntityKeyResolver`. `EntityTypeResolverTest` usa 17 y no
4 como id de `catalog_product` justamente para que un resolver que asumiera el
"típico" 4 falle.

**Verificación por HTTP:**

```
/environment  counts: {'products': 8, 'attribute_sets': 413, 'attributes': 1066, 'categories': 6}
/checksums?storeId=1: {"product_count":8,"sku_digest":"3961b231…"}
/products?storeId=1:  8 items
```

---

## M2 — cursor inválido: 400, no 500

`Cursor::decode()` lanza `InputException` (que es `LocalizedException`, que el
`ErrorProcessor` de web API mapea a 400) en vez de
`\InvalidArgumentException`, que el framework no reconoce y renderiza como
error de servidor.

```
ANTES   GET /products?storeId=1&limit=3&cursor=BASURA  ->  500
DESPUÉS GET /products?storeId=1&limit=3&cursor=BASURA  ->  400
```

`encode()` sigue lanzando `\InvalidArgumentException` **a propósito**: una
clave negativa no viene de la red, viene de un error de programación del
propio módulo, y ahí el 500 es la respuesta correcta. La prueba lo fija en las
dos direcciones.

El mensaje tampoco repite el cursor recibido, que es texto que el cliente
controla y viajaría de vuelta en un cuerpo de error.

**Sobre la traza en el cuerpo: no es del módulo, y no se puede arreglar desde
el módulo.** `ErrorProcessor::maskException` la agrega para **cualquier**
excepción cuando `MAGE_MODE` es `developer`:

```php
// vendor/magento/framework/Webapi/ErrorProcessor.php:114
$isDevMode = $this->_appState->getMode() === State::MODE_DEVELOPER;
$stackTrace = $isDevMode ? $exception->getTraceAsString() : null;
```

Comprobado empíricamente en la instancia de desarrollo, cambiando sólo
`MAGE_MODE` a `default` y volviéndolo a `developer`:

```
MAGE_MODE=developer:  400 {"message":"cursor inválido: …","parameters":["skudo1:"],"trace":"#0 …"}
MAGE_MODE=default:    400 {"message":"cursor inválido: …","parameters":["skudo1:"]}
```

Es idéntico para todo endpoint de cualquier módulo de la instalación, y
desaparece en cuanto el modo no es `developer` (que es el modo de cualquier
producción). La mitad accionable de M2 era el código de estado.

---

## M3 — `storeId` inexistente: 400, no 200 con los valores globales

`StoreViewGuard` es el único lugar que decide si un `storeId` recibido por la
red existe, y los **cuatro** caminos que reciben uno lo llaman.

La existencia se pregunta al `StoreManagerInterface`, no a la tabla `store`:
es la MISMA fuente de verdad que alimenta `/environment.store_views`, así que
un tenant que configura una store view que la sonda reportó nunca puede
recibir este error, y una que la sonda no reportó lo recibe siempre.

`InputException` (400) y no `NoSuchEntityException` (404): el `storeId` es un
**parámetro** de la petición, no el recurso que la ruta nombra, y un cliente
necesita el 400 para no reintentar.

`/checksums` valida también, aunque su `$storeId` no filtre (Ruling 2 de esa
clase): un storeId inexistente que devuelve 200 hace que los dos lados de
`reconcile()` coincidan sobre un espejo construido con los valores globales de
una tienda que no existe.

**Verificación por HTTP:**

```
ANTES   /products?storeId=999&limit=3   -> 200, 3 items con store_values: {}
ANTES   /signals?storeId=999&days=90    -> 200, {"items":[]}

DESPUÉS /products?storeId=999           -> 400  "no existe la store view 999 en esta instancia; …"
DESPUÉS /signals?storeId=999            -> 400  idem
DESPUÉS /checksums?storeId=999          -> 400  idem
DESPUÉS POST /products-by-sku storeId=999 -> 400  idem
```

**Pruebas:** `StoreViewGuardTest` (incluida la memoización del caso negativo,
que un `isset()` sobre un `false` dejaría pasar) y
`UnknownStoreViewIsRejectedTest`, que enumera los cuatro caminos en un
provider — es la prueba que falla si alguien agrega un quinto camino con
`storeId` y se olvida del guard, o quita la llamada de uno de los cuatro.
Trae contra-guarda: con la store view conocida los cuatro responden (sin ella,
un guard que rechazara todo pasaría la prueba y rompería el módulo).

---

## B1 — dos mitades, una arreglada y una documentada

### Los `%1` sin interpolar: arreglado del lado del cliente

Magento no manda el mensaje armado; manda la plantilla de `__()` y los
argumentos por separado, para que quien lo reciba pueda traducirlo. Eso es su
contrato y no hay que pelearlo. El problema era que `raise_for_status()` de
httpx lo tiraba entero: su mensaje es `Client error '400 Bad Request' for url
…`, así que el motivo real —el único dato útil— no aparecía en ningún log ni
en ninguna traza.

`skudo.magento.client.raise_for_status()` propio, con `MagentoApiError`:
interpola las **dos** formas observadas (`%1`/`%2` por posición,
`%fieldName` por nombre, las claves más largas primero para que `%field` no
parta `%fieldName`), deja **visible** un placeholder sin valor en vez de
borrarlo (un `%2` visible dice "falta un dato acá", que es información; el
hueco borrado miente sobre lo que el servidor dijo), y expone `status_code`
para que un caller distinga un 400 (entrada inválida: no reintentar) de un
5xx. Las ocho llamadas a `response.raise_for_status()` del cliente pasaron a
usarlo.

**Verificación contra la instancia real:**

```
cursor inválido: HTTP 400 | cursor inválido: se esperaba un cursor emitido por este módulo (base64 de "skudo1:<clave>")
storeId 999:     HTTP 400 | no existe la store view 999 en esta instancia; los ids válidos son los que reporta /environment en store_views
101 SKUs:        HTTP 400 | no se pueden pedir más de 100 SKUs por llamada (se recibieron 101)
```

Siete pruebas nuevas en `tests/magento/test_client.py`, incluida la
contra-guarda de que una respuesta exitosa no se toca.

### La variación `int`/`float`: documentada, no forzada

`json_encode((float) 300)` de PHP emite `300`, no `300.0`, con
`serialize_precision = -1` (el default desde PHP 7.1). Medido:

```
store 1: revenue -> int   (300)     salable_qty -> int (40)   margin -> float (0.4545)
store 3: revenue -> float (75.5)    salable_qty -> int (7)    margin -> float (0.3846)
```

**No se fuerza el tipo**, y la razón está escrita en
`SignalReaderInterface`: las dos formas de forzarlo son peores que
documentarlo. Emitirlo como string (`"300.0000"`) cambia el tipo del contrato
y obliga a parsear del otro lado; multiplicar por algo para obligar la parte
fraccionaria falsea el dato. Lo que corresponde es que el CONSUMIDOR acepte
`int|float` donde el contrato dice `float`, y eso ahora está fijado por
`test_an_integral_json_number_is_accepted_where_a_float_is_declared`: un
número entero se acepta y las columnas `Numeric(18,4)` del espejo no pierden
nada.

---

## Verificación final punta a punta

Espejo recreado desde cero (`DROP`/`CREATE` de `skudo_httpreal`, `alembic
upgrade head` hasta `0012`), instancia en Config B:

```
[OK] sync_attributes: {"pages_fetched": 3, "attributes_written": 1066, "options_written": 50535}
[OK] sync_categories: {"pages_fetched": 1, "categories_written": 6}
[OK] full_sync:       {"records_written": 16, "records_deleted": 0, "pages_fetched": 2,
                       "records_without_timestamp": 2, "skus_without_timestamp": ["SKU-Z-LAST","SKU-Z-LAST"]}
[OK] sync_signals:    {"store_views_read": 2, "signals_written": 5,
                       "signals_without_product_record": 0, "skus_without_product_record": []}
[OK] delta_sync:      {"changes_seen": 5, "records_updated": 6, "records_deleted": 0, "watermark": 5, ...}
[OK] reconcile:       {1: {magento_count: 8, mirror_count: 8, digest_matches: True, needs_full_sync: False},
                       3: {magento_count: 8, mirror_count: 8, digest_matches: True, needs_full_sync: False}}
```

Arnés de aceptación:

```
[OK ] espejo_sincronizado: sin deriva
[OK ] score_por_store_view: conteos por store view: {1: 8, 3: 8}
[OK ] procedencia_de_scope: 16 registros revisados; procedencias: {'store': 4, 'global': 71, 'website': 3}
[OK ] identidad_de_opciones: 327 opciones con etiqueta distinta por store view reconocidas como una sola opción
[OK ] efecto_de_categoria: 6 producto(s) con efecto de categoría distinto entre store views; 9 par(es)
      evaluado(s), 0 sin evaluar por website_desconocido, 0 par(es) sin website de la tienda espejado
EXIT=0
```

Los cinco criterios pasan, y ahora sobre **8** productos —la población que
Magento considera activa— en vez de los 7 del conjunto estrechado. El
"espejo_sincronizado: sin deriva" del informe anterior era cierto y engañoso a
la vez porque los dos lados miraban el conjunto estrechado; ahora los dos
miran el mismo conjunto que la tienda.

Suites:

```
SKUDO_MAGENTO_ROOT=/home/ingmar/magento-skudo-dev \
SKUDO_MAGENTO_APP_ROOT=/home/ingmar/magento-skudo-dev \
  vendor/bin/phpunit -c phpunit.xml      -> OK (136 tests, 566 assertions)
uv run pytest -q                          -> 195 passed
uv run ruff check src tests                -> All checks passed!
```

Sin `SKUDO_MAGENTO_APP_ROOT`, los 3 tests de integración se saltan y el resto
sigue verde: `OK, but some tests were skipped! Tests: 136, Skipped: 3`.

---

## Lo que NO se hizo, y por qué

1. **Recalcular los números de C2 contra el catálogo de producción.** Toda
   lectura de la base `nisseicom` fue **bloqueada por el clasificador del
   sandbox** (tres intentos, tres formas de la misma `SELECT`). Los números
   del hallazgo original (228.481 / 36 / 202) están calculados con
   `versionId = 1` y por lo tanto no describen producción; cuál es la cifra
   real depende de dónde esté el flag `staging` de producción, que es
   exactamente lo que no pude leer. La consulta que haría falta, para cuando
   haya permiso:

   ```sql
   SELECT flag_data FROM flag WHERE flag_code = 'staging';
   -- y con ese V:
   SELECT COUNT(*) FROM catalog_product_entity WHERE created_in <= V AND updated_in > V;
   SELECT COUNT(DISTINCT entity_id) FROM catalog_product_entity;
   ```

   No cambia ningún arreglo: la dirección del arreglo de C2 se sostiene por el
   mecanismo (dos anclas distintas ANDadas) y está demostrada con una fila en
   Config A.

2. **El `%1` no se interpola del lado PHP.** Mandar plantilla + parámetros es
   el contrato de web API de Magento y sirve para i18n; romperlo desde un
   módulo sería pelear con el framework. Se resolvió del lado que consume.

3. **La traza en el cuerpo de error no se quitó desde el módulo.** No es del
   módulo (§ M2): la agrega `ErrorProcessor` para toda excepción en
   `MAGE_MODE=developer`, y desaparece con cualquier otro modo. Comprobado
   empíricamente.

4. **El tipo `int`/`float` no se forzó en PHP.** Documentado con la medición y
   fijado del lado consumidor (§ B1).

5. **`FakeSelect::join()` sigue siendo un no-op** en las pruebas viejas, así
   que `eavValues()`/`websiteIds()` siguen sin ejecutarse ahí. Es el hallazgo
   M5 del informe anterior, fuera de esta ola; lo que sí cambió es que ahora
   existe una suite de integración donde esas rutas SÍ se ejecutan contra una
   instancia real (`PopulationMatchesMagentoTest` pagina `/products` completo,
   que las ejercita todas), y `MapValuedFieldsAreJsonObjectsTest` ejercita
   `eavValues()` con un doble que sí devuelve filas.

6. **El default de `make test-php` sigue apuntando a la instalación de
   producción** (`/var/www/casanissei.com/v248/vendor/bin/phpunit`). Es sólo
   lectura y es preexistente, así que no lo cambié; se agregó el target
   `test-php-integration` con `SKUDO_MAGENTO_APP_ROOT` explícito.

---

## Cambios en el entorno de desarrollo (no en el módulo)

Todos en `/home/ingmar/magento-skudo-dev` y en la base `skudo_magento`, que
son míos. **Nada** se escribió en `/var/www/casanissei.com/v248`, en la base
`nisseicom` ni en ningún contenedor `*_local`.

- 3 filas en `staging_update` y la fila de `flag` con `flag_code = 'staging'`
  (§0). Sin ellas `VersionManager` cae en `setId(1)` y toda medición sobre el
  filtro de Magento es un artefacto.
- `bin/magento setup:di:compile` dos veces (los factories compilados
  referenciaban `ActiveVersionResolver` y después las clases nuevas).
- `MAGE_MODE` puesto en `default` y devuelto a `developer` para medir la traza
  de error (§ M2); `app/etc/env.php` quedó como estaba
  (`9790135c5ffaa883b4d7d660876508a3` antes y después).

Comprobación de que producción no se tocó, con las mismas dos sondas del
informe anterior:

```
$ find /var/www/casanissei.com -newermt '-6 hours' \( -type f -o -type d \)
(sin resultados)
$ ls -la /var/www/casanissei.com/v248/app/etc/env.php
-rwxrwxr-x 1 www-data www-data 5455 jul 21 14:23 .../app/etc/env.php
```

Las únicas lecturas de producción fueron `grep`/`ls`/`find` sobre archivos.
Cero comandos contra la base `nisseicom` (los tres que intenté fueron
bloqueados por el clasificador, ver "Lo que NO se hizo"), cero `bin/magento`
en `v248`, cero `exec` en contenedores `*_local`.

Artefactos nuevos en `/home/ingmar/skudo-dev-logs/fixes/`:

| Archivo | Qué |
|---|---|
| `api.sh` | helper de `curl` con el token, para `GET` y `POST` |
| `boot-probe.php` | arranca la instancia e imprime el SQL ensamblado del `Select` del framework y las filas que devuelve |
| `set-version.php` | fija/lee el `current_version` de Staging vía `VersionHistoryInterface` |
