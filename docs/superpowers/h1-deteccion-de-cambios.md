# H1 — Detección de cambios: las escrituras masivas y la reconciliación de contenido

Fecha: 2026-09-10. Cierre del hallazgo **H1** de `docs/superpowers/pendiente-s0.md`,
el único defecto del sub-proyecto que fallaba **en silencio y de forma
permanente**: la cola de cambios no veía las escrituras masivas, y la
reconciliación —que compara el conjunto de SKUs y su huella— no podía
detectarlo, ni al décimo ciclo ni al año.

**Estado en una línea:** cerrado en dos mitades, y la mitad importante es la
segunda. Los dos eventos de escritura masiva quedan observados, y `/checksums`
publica un **digest de contenido por partición** que `reconcile()` recalcula y
compara, reportando **qué particiones divergen** en vez de un
`needs_full_sync` de todo-o-nada. Todo verificado sobre HTTP real contra la
instancia de desarrollo, con el experimento del paso 1 ejecutado antes de
diseñar nada.

| | |
|---|---|
| Commits | `8752c6f` (observers), `74a5f73` (digest de contenido), `bafdad8` (reconcile) |
| Suites | **174 PHPUnit** (170 unitarias + 4 de integración), **210 pytest**, `ruff` limpio |
| Verificación | HTTP real contra `127.0.0.1:8088`, espejo `skudo_httpreal`, más el experimento sobre la instancia |

---

## 1. El experimento, que fue lo primero

La pregunta que decidía la forma del arreglo, planteada antes de escribir una
línea de diseño: **¿se mueve `catalog_product_entity.updated_at` en todos los
caminos de escritura?**

No se razonó desde el código: se ejecutó cada camino contra la instancia de
desarrollo y se observó `updated_at` antes y después, más las filas que cada
camino dejó en `standard_skudo_change_log` y la evidencia de que la escritura
ocurrió de verdad. El script es
`/home/ingmar/skudo-dev-logs/h1/updated-at-experiment.php` (y
`h1/import-experiment.php` para el camino de importación).

La columna, en esta instancia:

```
`updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
```

El `ON UPDATE CURRENT_TIMESTAMP` resulta importante y no era obvio: acota el
piso del sistema más abajo de lo que "SQL directo" sugiere.

### 1.1 La salida cruda, camino por camino

Cada bloque es la salida literal del script. `se movio=SI/NO` compara el
`updated_at` de la misma fila antes y después.

**`Product\Action::updateAttributes()`** — la acción masiva "Actualizar
atributos" del grid de admin y la API que usan las herramientas de carga por
lotes de terceros (esta tienda corre Amasty):

```
=== Product\Action::updateAttributes(entity_id=1001, name, store 0) ===
evidencia de la escritura: name global de row_id 101 = 'Alpha global tocado por updateAttributes 1789070143'
  row_id=101 entity_id=1001 sku=SKU-ALPHA created_in=1 updated_in=2147483647  updated_at ANTES=2026-09-01 08:30:00 DESPUES=2026-09-10 19:55:43  se movio=SI
  filas nuevas en la cola de cambios: NINGUNA
```

**`Product\Action::updateWebsites()`**:

```
=== Product\Action::updateWebsites(entity_id=1004, website 2, add) ===
evidencia de la escritura: catalog_product_website ANTES=["1"] DESPUES=["1","2"]
  row_id=104 entity_id=1004 sku=SKU-DELTA created_in=1 updated_in=2147483647  updated_at ANTES=2026-09-04 08:30:00 DESPUES=2026-09-04 08:30:00  se movio=NO
  filas nuevas en la cola de cambios: NINGUNA
```

**`ProductRepository::save()`** — el camino normal del modelo:

```
=== ProductRepository::save() (camino normal del modelo) ===
evidencia de la escritura: name global de row_id 102 = 'Beta tocado por save del modelo 1789070273'
  row_id=102 entity_id=1002 sku=SKU-BETA created_in=1 updated_in=2147483647  updated_at ANTES=2026-09-02 08:30:00 DESPUES=2026-09-10 19:57:53  se movio=SI
  filas nuevas en la cola de cambios: [{"change_id":"6","sku":"SKU-BETA","event":"save"}]
```

**`CatalogImportExport`** — importación por lotes, ejecutada de verdad con el
mismo modelo `Magento\ImportExport\Model\Import` que usa el admin (behavior
`append`, un CSV de una fila con un SKU existente):

```
CSV: sku,name
"SKU-NORMAL","Normal importado por CatalogImportExport 1789070332"
validateSource: true
importSource: true
creados=0 actualizados=1

=== CatalogImportExport (behavior append, 1 fila, sku existente) ===
  row_id=104 sku=SKU-DELTA   updated_at ANTES=2026-09-04 08:30:00 DESPUES=2026-09-04 08:30:00 se movio=NO
  row_id=107 sku=SKU-NORMAL  updated_at ANTES=2026-09-10 19:56:23 DESPUES=2026-09-10 19:58:53 se movio=SI
  filas nuevas en la cola: NINGUNA
  name global de SKU-NORMAL = 'Normal importado por CatalogImportExport 1789070332'
```

(La fila de `SKU-DELTA` está en la salida como control: no estaba en el CSV y
no se movió, así que lo que movió a `SKU-NORMAL` fue la importación y no el
mero hecho de correrla.)

Coincide con el código: `Model/Import/Product.php:1770` mete
`'updated_at' => (new \DateTime())->format(...)` en la fila de entidad de
**todo** SKU existente del lote, y `:1474` lo escribe con
`insertOnDuplicate($entityTable, $entityRowsUp, ['updated_at', 'attribute_set_id'])`.
Es decir: la importación mueve `updated_at` de cada fila del CSV **incluso si
ningún valor cambió**. Eso hace al digest de contenido ruidoso frente a una
importación idempotente —reportará divergencia de las particiones tocadas—
pero nunca ciego, que es la asimetría correcta.

**SQL directo a una tabla de valores EAV** — el caso piso:

```
=== UPDATE de SQL directo sobre catalog_product_entity_varchar (el CASO PISO) ===
evidencia de la escritura: name global de row_id 105 = 'Alpha minusculas pisado por SQL directo 1789070182'
  row_id=105 entity_id=1005 sku=sku-alpha-lower created_in=1 updated_in=2147483647  updated_at ANTES=2026-09-05 08:30:00 DESPUES=2026-09-05 08:30:00  se movio=NO
  filas nuevas en la cola de cambios: NINGUNA
```

**SQL directo a la fila de entidad** — el resultado que corrige la intuición:

```
=== UPDATE de SQL directo sobre catalog_product_entity (columna que cambia de verdad) ===
evidencia de la escritura: has_options invertido; el UPDATE que no cambia nada dejo updated_at en 2026-09-07 08:30:00
  row_id=107 entity_id=1007 sku=SKU-NORMAL created_in=1 updated_in=2147483647  updated_at ANTES=2026-09-07 08:30:00 DESPUES=2026-09-10 19:56:23  se movio=SI
  filas nuevas en la cola de cambios: NINGUNA
```

Dos cosas en ese último bloque: un `UPDATE` que cambia una columna de la fila
de entidad **sí** mueve `updated_at`, por el `ON UPDATE CURRENT_TIMESTAMP`; y
un `UPDATE` que no cambia ningún valor (`SET attribute_set_id = attribute_set_id`)
**no** lo mueve, porque MySQL no considera modificada la fila.

### 1.2 La tabla que salió del experimento

| camino de escritura | ¿escribió? | ¿movió `updated_at`? | ¿dejó fila de cola? (antes del arreglo) |
|---|---|---|---|
| `ProductRepository::save()` | sí | **SÍ** | sí (`save_after`) |
| `Product\Action::updateAttributes()` | sí | **SÍ** (Magento lo escribe explícito) | **NO** |
| `Product\Action::updateWebsites()` | sí | **NO** | **NO** |
| `CatalogImportExport` (append) | sí | **SÍ** (lo escribe explícito) | **NO** |
| `UPDATE` directo a `catalog_product_entity` | sí | **SÍ** (`ON UPDATE CURRENT_TIMESTAMP`) | **NO** |
| `UPDATE` directo a `catalog_product_entity_varchar` | sí | **NO** | **NO** |
| `UPDATE` directo que no cambia ningún valor | no | NO | NO |

### 1.3 La respuesta, y qué decidió

**`updated_at` NO se mueve en todos los caminos que pasan por PHP.** La
hipótesis optimista del brief —"si se mueve en todos, un digest de
`(sku, updated_at)` es un detector completo salvo para SQL crudo"— es falsa
por un caso, y ese caso es exactamente uno de los dos que hay que observar:
`updateWebsites()`.

De ahí las dos mitades del arreglo, con su alcance decidido por la medición y
no al revés:

1. **Observers** para los dos eventos que los caminos masivos sí despachan.
   `updateWebsites()` los necesita **de forma insustituible**: ningún digest
   de `(sku, updated_at)` puede ver ese cambio. `updateAttributes()` los
   necesita por **latencia**: el digest lo detectaría en la próxima
   reconciliación, el observer en segundos, y la reconciliación es caro
   correrla seguido.
2. **Digest de contenido por partición**, que cubre lo que ningún observer
   puede: la importación por lotes (que escribe por debajo del modelo) y el
   `UPDATE` de SQL directo a la fila de entidad.

Y el **piso declarado**: un `UPDATE` de SQL directo a una tabla satélite
(valores EAV, `catalog_product_website`, `catalog_category_product`) no deja
rastro en ninguna parte del esquema de Magento. Nada puede detectarlo, y
saberlo acota el diseño en vez de fingir que no está. El piso es más bajo que
"SQL crudo" y más alto de lo que parecía: **SQL crudo a la fila de entidad SÍ
se detecta**.

### 1.4 Defectos del ENTORNO que hubo que arreglar para poder medir

Cuatro huecos del volcado selectivo con que se construyó la base de
desarrollo, de la misma clase que el `staging_update` vacío que documentó
`verificacion-http-real-fixes.md` §0. Los cuatro hacían **abortar** el camino
que se quería medir, no dar un resultado equivocado, así que ninguna medición
anterior queda en duda por esto. Todos en `skudo_magento`, que es mía:

| Hueco | Qué rompía | Arreglo |
|---|---|---|
| `sequence_product` sin los ids sembrados (1001-1009) | `updateWebsites()` fallaba con FK 1452 contra `sequence_product` | `INSERT IGNORE` de los 9 ids |
| `cataloginventory_stock` vacía | `ProductRepository::save()` fallaba con FK 1452 al guardar el stock item | fila `(1, 0, 'Default')` |
| `inventory_source` vacía | el mismo save fallaba después en MSI, FK contra `inventory_source` | fila `default` |
| `sequence_catalog_category` sin 10/11/12/99 | el mismo save fallaba al generar url rewrites de categoría | `INSERT IGNORE` de los 6 ids |
| sólo `SKU-ALPHA`/`SKU-BETA` tenían `url_key` global | `updateWebsites()` fallaba en un plugin de url rewrite (`GetProductUrlRewriteDataByStore` devolvía null) | `url_key` global para los 8 productos restantes |

Ninguno es un cambio en el módulo ni en el ingestor. **Nada se escribió en
`/var/www/casanissei.com/v248`, en la base `nisseicom` ni en ningún contenedor
`*_local`.**

---

## 2. Parte 1 — Los observers de las escrituras masivas

### 2.1 Qué se suscribió, y por qué esos dos eventos

`etc/events.xml` pasa de dos eventos a cuatro:

| evento | quién lo despacha | por qué hace falta |
|---|---|---|
| `catalog_product_save_after` | el modelo | ya estaba |
| `catalog_product_delete_after` | el modelo | ya estaba |
| `catalog_product_attribute_update_before` | `Product\Action::updateAttributes()` (`Action.php:85`) | es lo **único** que despacha ese camino |
| `catalog_product_to_website_change` | `Product\Action::updateWebsites()` (`Action.php:168`) | es lo único que despacha, y el único detector posible de ese cambio |

`Observer\ProductBulkChanged` atiende los dos. Tres decisiones que vale
nombrar:

**`_before` y no `_after`.** Es el único que Magento despacha en el camino de
`updateAttributes()`. Se registra por lo tanto la *intención* y no el hecho: si
la escritura falla o revierte después, queda una fila de cola que provoca el
refresco de un producto que no cambió. Es inofensivo —todo el camino de
refresco del ingestor es upsert por `(tenant, sku, store view)`— y es la
asimetría correcta: un refresco de más cuesta una petición, un cambio perdido
cuesta un valor rancio permanente.

**Se leen las dos claves de carga, no se ramifica por nombre de evento.**
`product_ids` en uno, `products` en el otro. Si Magento renombrara la clave en
una versión futura, no queda un observer que registra cero en silencio: queda
una advertencia en el log que lo nombra.

**Nunca se queda callado.** Un observer que no registra nada y no lo dice
simula cobertura, que es peor que no tenerlo. Se registra advertencia cuando el
evento llega sin ninguna clave conocida y cuando un id no resuelve a ningún
SKU, nombrando los ids. Una selección vacía, en cambio, no avisa: no hubo
escritura que observar, y un aviso por cada no-evento entrena a quien lee el
log a ignorarlo.

### 2.2 La traducción de id a SKU, que es donde estaba el riesgo

Los eventos llevan **ids** y la cola guarda **SKUs**. `Model\SkuResolver` hace
la traducción y empareja **siempre por `entity_id`**, nunca por la clave de la
tabla.

Que el contrato del evento sea `entity_id` no es una suposición: el propio
Magento lo trata así. `ResourceModel\Product\Action::resolveEntityId()`
(`:231-241`) recibe el id del evento y lo traduce a la clave con
`WHERE entity_id = ?`. Si le pasaras un `row_id`, esa búsqueda no encontraría
nada.

El modo de fallo que se evita: bajo Magento_Staging la clave es `row_id`, y los
dos espacios de id se solapan (en esta instancia los `row_id` son 101-110 y los
`entity_id` 1001-1009, deliberadamente distintos; en producción se cruzan). Un
resolutor que emparejara por la clave **no falla**: devuelve el SKU de **otro
producto**. La cola quedaría con un cambio anotado sobre un producto que nadie
tocó, y el que cambió de verdad no se refrescaría nunca. Es el mismo modo de
fallo que H1, dentro del arreglo de H1.

`SkuResolverTest::testIdsAreMatchedByEntityIdAndNeverByTheEntityKey` usa una
fixture donde el `row_id` de un producto (1001) es **también** el `entity_id`
de otro. Sabotaje (emparejar por la clave):

```
--- Expected
+++ Actual
-    0 => 'SKU-ENTIDAD-1001',
+    0 => 'SKU-DE-LA-FILA-1001',
```

La prueba nombra el SKU equivocado, no sólo "algo falló".

`EntityKeyResolver` se consulta —y no se ignora la pregunta de esquema— porque
su respuesta explica el otro caso: con la clave en `row_id`, un mismo
`entity_id` puede tener varias filas de versión. Magento las acota a la
aplicada, así que en la práctica vuelve una; cuando vuelven varias con SKUs
distintos (un cambio de SKU programado) se devuelven **todos** los SKUs
distintos, por la misma asimetría de coste.

### 2.3 `ChangeLog::recordMany()`

La cola tenía un solo camino de escritura, una fila por llamada. Una acción del
grid sobre una selección entera son miles de `INSERT` dentro de la petición del
admin: el observer se vuelve el coste dominante de la operación que observa, y
un observer caro es un observer que alguien desactiva — y con eso desaparece la
única señal de esas escrituras. `recordMany()` usa `insertMultiple` en lotes de
1000 (un lote entero en una sentencia choca con `max_allowed_packet`, y ese
error llegaría como excepción dentro de la acción de admin del cliente).

### 2.4 La prueba del cableado

`Test/Unit/EventWiringTest` afirma `etc/events.xml` en sí: qué eventos se
escuchan, con qué clase, que la clase existe e implementa `ObserverInterface`,
y —contra-guarda— que el archivo no declara nada que la prueba no conozca.

Existe porque **el defecto H1 fue exactamente eso**: un observer correcto,
probado y verde, suscrito a los eventos equivocados. Ninguna prueba de la clase
podía notarlo.

---

## 3. Parte 2 — El digest de contenido por partición

### 3.1 El esquema

`Model\ContentDigest` (PHP) y las funciones homónimas de
`src/skudo/ingest/reconcile.py` (Python):

```
partición(sku) = los dos primeros caracteres hex de sha256(bytes UTF-8 del sku)
              -> 256 particiones, '00'..'ff'

digest(partición) = sha256( "\n".join( sorted( [ f"{sku}\t{token}" ] ) ) )

token(updated_at) = los primeros 19 caracteres si tienen forma
                    'YYYY-MM-DD HH:MM:SS' y no empiezan en '0000-00-00';
                    si no, el literal 'desconocido'
```

`/checksums` publica, además del `product_count` y el `sku_digest` que ya
tenía:

```json
{"product_count": 8,
 "sku_digest": "3961b231…2a2a65",
 "partition_count": 256,
 "content_partitions": [{"partition": "1e", "product_count": 1, "content_digest": "286ee242…2e2d4e"}, …]}
```

### 3.2 Por qué es reproducible idéntico en los dos lados

La partición depende **sólo de los bytes UTF-8 del SKU**. No de:

- **ids de entidad** — el espejo no guarda `row_id` ni `entity_id`, así que
  cualquier esquema por rango de clave sería incomputable de este lado. Ésta
  fue la razón de elegir hash del SKU y no rango de clave.
- **el orden de ninguna consulta** — las líneas se ordenan después, en el
  lenguaje, con `sort($lines, SORT_STRING)` y `sorted(lines)`, que sobre UTF-8
  comparan igual byte a byte. Es el Ruling 1, ya validado empíricamente con
  mayúsculas, minúsculas y un acento.
- **la colación de MySQL** — `sku` es `utf8mb4_general_ci`, que ordena sin
  distinguir mayúsculas ni acentos; un `ORDER BY` de SQL daría otra secuencia.
  `partition_of("SKU-ALPHA") != partition_of("sku-alpha")` está afirmado en las
  dos suites: son productos distintos y caen en particiones distintas, que es
  lo correcto y lo que una colación case-insensitive no podría reproducir.
- **la zona horaria** — ver §4.
- **ninguna configuración** — `PARTITION_COUNT` es una constante en los dos
  lados, no un parámetro. Un número configurable es una forma de que los dos
  lados particionen distinto y comparen manzanas con naranjas. Viaja en el
  payload (`partition_count`) para que el lado Python pueda **exigir** el
  acuerdo, y `reconcile()` aborta si no lo hay.

`hash('sha256', $sku)` de PHP y `hashlib.sha256(sku.encode("utf-8")).hexdigest()`
de Python dan la misma cadena hex para los mismos bytes, y el SKU viaja como
texto sin normalizar por todo el sistema (ya verificado sobre HTTP real con
`SKU-ÑOÑO`, ida y vuelta intacto, hasta la fila del espejo).

El acuerdo está **fijado con constantes cruzadas**: los cuatro digests de
`ContentDigestTest::testPartitionDigestsMatchThePythonAlgorithm` se calcularon
en Python y están escritos literalmente en el archivo PHP; los mismos cuatro
están en `test_the_partition_digest_matches_the_php_algorithm` del lado Python.
Cambiar el algoritmo en un solo lado rompe las dos suites.

Confirmación cruzada con datos reales, no de fixture: la partición `eb` que
`/checksums` devolvió sobre HTTP real
(`273148f21b478033b76bff8f276b36f1ceb058765d3096c316634aa7ae0e241d`) es
exactamente el valor que la prueba PHP espera para `SKU-ÑOÑO` con
`updated_at = 2026-09-06 08:30:00`, que es el dato que esa instancia tiene.

### 3.3 Por qué 256, y por qué particionar

El remedio de una huella global es "re-sincronizá todo": en 228.881 productos
son horas, y es **la misma respuesta** si derivó un producto o si derivaron
todos. Con 256 particiones, una partición del catálogo piloto son ~894
productos: un remedio dirigido, y además una pista de en qué cohorte se está
portando mal el proceso de aguas arriba, que es lo que el spec §7.6 pide
("se agrupa por cohorte y por campo para señalar la causa").

El payload cuesta poco: sólo las particiones **no vacías**, ordenadas. Con 8
productos son 8 filas; con 228.881 serían 256 filas de ~80 bytes.

### 3.4 Qué entra en el digest, y qué NO puede ver

Entra el par `(sku, updated_at)`. **No** los valores EAV: traerlos para 228.881
productos × 1.066 atributos en un endpoint de reconciliación sería más caro que
re-sincronizar, y el endpoint dejaría de ser una red de seguridad para
convertirse en el problema.

El límite está **medido** (§1.2) y escrito en el docblock de `ContentDigest`,
no supuesto. Lo que el digest no ve:

- **`Action::updateWebsites()`** — lo cubre el observer de la parte 1. Es la
  razón por la que ese observer sigue haciendo falta aunque exista el digest.
- **`UPDATE` de SQL directo a una tabla satélite** — el piso. Nada lo detecta.

### 3.5 `content_partitions` es una LISTA, no un mapa

Un array PHP con claves `'00'`, `'10'`, `'ff'` es de claves **mixtas** (PHP
convierte `'10'` en int y deja `'00'` como string), y `json_encode` de eso
emite objeto o array según los datos: es el bug de `labels`,
`global_values` y `store_values` en su cuarta forma posible. Una lista de
objetos no tiene ese problema en ninguna forma.

Vale registrar que **el barrido de C1 lo detectó solo**:
`MapValuedFieldsAreJsonObjectsTest::testEveryObjectShapedFieldIsClassified`
falló al agregar el campo, nombrándolo (`checksums:content_partitions[*]`), y
obligó a clasificarlo. La regla dejó de vivir en un docblock y ahora se aplica
sobre el conjunto.

---

## 4. La trampa: el acuerdo TEXTUAL del `updated_at`

Es donde este proyecto ya se equivocó dos veces (el filtro de versión, el
`ORDER BY`), y el modo de fallo es el mismo: un digest que discrepa por una
diferencia de forma reportaría **deriva permanente**, y el remedio que
prescribe —re-sincronizar— **no la limpiaría nunca**, porque después del
re-sync los dos lados seguirían escribiendo el texto distinto.

El riesgo es concreto: el espejo **no guarda el texto** que mandó Magento.
Guarda un `timestamptz` de Postgres al que llegó parseándolo, y psycopg lo
devuelve con el offset de la sesión de Postgres.

### 4.1 Cómo se verificó

`/home/ingmar/skudo-dev-logs/h1/updated-at-textual-agreement.py` compara, dato
por dato, cuatro cosas: el texto que MySQL devuelve, el texto que `/products`
entrega sobre HTTP real, el digest que `/checksums` publica, y el token que el
espejo produce desde el `timestamptz`. Y lo hace **con la sesión de Postgres en
`America/Asuncion`**, no en UTC, porque con la sesión en UTC el
`astimezone(UTC)` del lado Python sería un no-op y la verificación no probaría
nada.

Salida (recortada a lo que importa):

```
== 1. MySQL: time_zone de la sesion: SYSTEM  (= UTC en este contenedor)
   SKU-ALPHA          '2026-09-10 20:06:12'
   sku-alpha-lower    '2026-09-05 08:30:00'
   SKU-ÑOÑO           '2026-09-06 08:30:00'
   SKU-Z-LAST         '0000-00-00 00:00:00'

== 2. /products (HTTP real): el campo updated_at tal como llega
   SKU-ALPHA          '2026-09-10 20:06:12'
   SKU-ÑOÑO           '2026-09-06 08:30:00'
   SKU-Z-LAST         '0000-00-00 00:00:00'

== 3. Postgres: sesion en America/Asuncion y el valor que devuelve psycopg
   TimeZone de la sesion: America/Asuncion
   now() de la sesion:    2026-09-10 17:18:53.303886-03:00
   SKU-ALPHA          datetime.datetime(2026, 9, 10, 17, 6, 12, tzinfo=ZoneInfo('America/Asuncion'))
   sku-alpha-lower    datetime.datetime(2026, 9, 5, 5, 30, tzinfo=ZoneInfo('America/Asuncion'))
   SKU-Z-LAST         None

== 4. El token de cada lado, dato por dato
   sku                token del espejo (Python)    particion  digest coincide
   SKU-ALPHA          2026-09-10 20:06:12          24          SI
   sku-alpha-lower    2026-09-05 08:30:00          1e          SI
   SKU-BETA           2026-09-10 19:57:53          a7          SI
   SKU-DELTA          2026-09-04 08:30:00          7b          SI
   SKU-GAMMA          2026-09-03 08:30:00          5b          SI
   SKU-ÑOÑO           2026-09-06 08:30:00          eb          SI
   SKU-NORMAL         2026-09-10 19:58:53          49          SI
   SKU-Z-LAST         desconocido                  7a          SI

== 5. TODOS los digests coinciden con la sesion de Postgres en America/Asuncion -> True
```

Nótese el punto crítico: Postgres devuelve **17:06:12** para lo que MySQL
llamó **20:06:12**, y el token vuelve a ser `20:06:12`. El
`astimezone(UTC)` es lo que cierra el círculo.

### 4.2 La contra-prueba, que es la parte que convence

Quitado el `astimezone(UTC)` y repetido el mismo script contra la misma
instancia:

```
### CONTRA-PRUEBA: sin astimezone(UTC) ###
   sku                token del espejo (Python)    particion  digest coincide
   SKU-ALPHA          2026-09-10 17:06:12          24          NO
   sku-alpha-lower    2026-09-05 05:30:00          1e          NO
   SKU-BETA           2026-09-10 16:57:53          a7          NO
   SKU-DELTA          2026-09-04 05:30:00          7b          NO
   SKU-GAMMA          2026-09-03 05:30:00          5b          NO
   SKU-ÑOÑO           2026-09-06 05:30:00          eb          NO
   SKU-NORMAL         2026-09-10 16:58:53          49          NO
   SKU-Z-LAST         desconocido                  7a          SI
   TODOS los digests coinciden -> False
```

**7 de 8 particiones divergen, para siempre.** La única que "coincide" es la
del SKU sin fecha, cuyo token es `desconocido` de los dos lados. Y el
`reconcile()` completo, con la sesión en Asunción y sin el `astimezone`,
reporta las 7 particiones con `magento_digest` y `mirror_digest` distintos y
`digest_matches: True` — el falso positivo permanente exacto que se quería
evitar, producido a voluntad y después cerrado.

La misma propiedad queda fijada en la suite por
`test_the_token_is_the_utc_rendering_whatever_the_session_offset`, con una
guarda contra la vacuidad (`assert el_mismo_instante_en_asuncion.hour == 4`)
para que la fixture no pueda dejar de tener desplazamiento sin que se note.

### 4.3 Las otras dos formas del mismo riesgo, cerradas por construcción

- **Fracción de segundo.** `catalog_product_entity.updated_at` es `timestamp`
  sin precisión fraccionaria, así que en Magento tal como se instala no pasa;
  pasa si un tenant alteró la columna. Si la fracción hiciera fallar el parseo
  del lado Python, el espejo guardaría `NULL`, su token sería `desconocido`
  contra un timestamp real del otro lado, y la deriva sería permanente. Los dos
  lados **descartan** la fracción con la misma regla:
  `ContentDigest::timestampToken()` toma los primeros 19 caracteres y
  `parse_magento_datetime` acepta el formato con `%f` y hace
  `replace(microsecond=0)`. Coincidir al segundo es mejor que discrepar para
  siempre.
- **El cero-date.** `'0000-00-00 00:00:00'` (que MySQL admite con el `sql_mode`
  de esta tienda y que los catálogos heredados contienen: `SKU-Z-LAST` lo
  tiene) es `desconocido` en los dos lados. No una fecha de relleno, que sería
  un dato falso con aspecto confiable; no la cadena vacía, que se confundiría
  con "no vino el campo". El token no puede colisionar con ningún timestamp.

### 4.4 Un falso positivo del propio script, aclarado

En el bloque 1 de §4.1, un listado ingenuo de MySQL ordenado por `row_id`
muestra `SKU-GAMMA` con `updated_at = 2026-12-01 08:30:00`, y `/products`
muestra `2026-09-03 08:30:00`. **No es una discrepancia**: la consulta cruda ve
las dos filas de versión de esa entidad (`row_id` 103 vigente y 110 futura) y
el diccionario del script se queda con la última; `/products` y `/checksums`
ven la versión **aplicada**, que es la 103. Que los digests coincidan lo
demuestra.

---

## 5. Verificación sobre HTTP real de cada arreglo

Instancia de desarrollo en Config B (`current_version = 1789025363`), espejo
`skudo_httpreal` recreado desde cero (`alembic upgrade head` hasta `0012`),
`setup:di:compile` corrido después de cada cambio de constructor.
Script: `/home/ingmar/skudo-dev-logs/h1/scenarios.sh`.

### 5.0 El payload nuevo, sobre HTTP

```
GET /rest/V1/skudo/checksums?storeId=1  -> 200
[{"product_count":8,
  "sku_digest":"3961b231a5be237d7522fbdd39899f0e3dd044fa2c0f5ab841d5e173922a2a65",
  "partition_count":256,
  "content_partitions":[
    {"partition":"1e","product_count":1,"content_digest":"286ee2421065b436fdfb239d9c1d4dac8fb46cdfcdfd299b3ecd6ffcd32e2d4e"},
    {"partition":"24","product_count":1,"content_digest":"448ee00f7d1c270f17ac71eef171202dc7c1fbb83c238d4b759a0bcb2fcfa79e"},
    {"partition":"49","product_count":1,"content_digest":"9ec627947e6b46c8b67621365f67c47af2c74aac1adbe25facb6cd276acfaffb"},
    {"partition":"5b","product_count":1,"content_digest":"c445d256a5155026c221bb9d0d70ec0bf7bd31dfed8327ede657aa4bc9172584"},
    {"partition":"7a","product_count":1,"content_digest":"79fbeac9ecca2f57aa64996fe5b73acb33215c8a4376005535e2dff8ff208705"},
    {"partition":"7b","product_count":1,"content_digest":"ad9ba9a5cf59619c493b23dc770cf2f55f1fc4c46013c636a710d1113849ffcf"},
    {"partition":"a7","product_count":1,"content_digest":"ff23813c7123bb92da3607fb96b72ace347a69b22d9c2124e13b183a6e683d4d"},
    {"partition":"eb","product_count":1,"content_digest":"273148f21b478033b76bff8f276b36f1ceb058765d3096c316634aa7ae0e241d"}]}]
```

El envoltorio de B1 sobrevive al campo nuevo (lista de un objeto, claves de
primer nivel intactas) y el cliente lo desenvuelve sin lanzar.

### 5.1 Escenario A — `updateAttributes()`: lo ven el digest **y** el observer

```
=== Product\Action::updateAttributes(entity_id=1001, name, store 0) ===
  row_id=101 sku=SKU-ALPHA  updated_at ANTES=2026-09-10 20:06:12 DESPUES=2026-09-10 20:19:49  se movio=SI
  filas nuevas en la cola de cambios: [{"change_id":"9","sku":"SKU-ALPHA","event":"save"}]

--- A.1 reconcile ANTES de delta_sync (sólo el digest de contenido) ---
[OK] reconcile: {1: {..., 'digest_matches': True, 'needs_full_sync': False,
                     'partition_count': 256, 'partitions_compared': 8,
                     'content_matches': False,
                     'diverging_partitions': [{'partition': '24', 'magento_count': 1, 'mirror_count': 1,
                       'magento_digest': '45d519d1…a00cfe', 'mirror_digest': '448ee00f…cfa79e'}]}, 3: {…igual…}}

--- A.2 /deltas (la cola, gracias al observer nuevo) ---
[{"items":[{"change_id":9,"sku":"SKU-ALPHA","event":"save","changed_at":"2026-09-10 20:19:49"}],"last_change_id":9}]

--- A.3 delta_sync y reconcile DESPUES ---
[OK] delta_sync: {"changes_seen": 1, "records_updated": 2, "watermark": 9}
[OK] reconcile: {1: {…, 'content_matches': True, 'diverging_partitions': []}, 3: {…}}
```

Lo que hay que leer: `digest_matches: True` y `needs_full_sync: False` —el
conjunto de SKUs no cambió, que es **el punto**— con `content_matches: False`
y **una** partición nombrada. Antes de H1 esta línea decía "sin deriva".

También se verificó que el observer resolvió `entity_id 1001` al SKU correcto
(`SKU-ALPHA`) y no al de la fila `row_id 1001`.

### 5.2 Escenario B — `updateWebsites()`: el digest NO lo ve; el observer sí

El caso que justifica la parte 1. Se quita el website 2 de `SKU-DELTA`: un
producto que **sale de la tienda de Brasil**.

```
websites ANTES=["1","2"] DESPUES=["1"]
updated_at ANTES=2026-09-04 08:30:00 DESPUES=2026-09-04 08:30:00  se movio=NO
cola: [{"change_id":"11","sku":"SKU-DELTA","event":"save"}]

--- reconcile: el digest de contenido NO lo ve (punto ciego DECLARADO) ---
content_matches': True
content_matches': True

--- el espejo ANTES de delta_sync ---
    sku    | store_view_magento_id | website_ids
-----------+-----------------------+-------------
 SKU-DELTA |                     1 | [1, 2]
 SKU-DELTA |                     3 | [1, 2]

--- delta_sync (la única señal es la fila de cola del observer) ---
[OK] delta_sync: {"changes_seen": 1, "records_updated": 2, "watermark": 11}

--- el espejo DESPUES ---
    sku    | store_view_magento_id | website_ids
-----------+-----------------------+-------------
 SKU-DELTA |                     1 | [1]
 SKU-DELTA |                     3 | [1]
```

Sin el observer, el espejo seguiría afirmando que `SKU-DELTA` pertenece al
website 2 —y `derive_category_effect` seguiría diciendo que el producto es
accesible en Brasil— **indefinidamente**, con la reconciliación reportando
"sin deriva" en cada ciclo. `website_ids` es, además, la única condición que
discrimina PY de BR en este tenant.

### 5.3 Escenario C — SQL directo, el piso

**C.1, `UPDATE` a `catalog_product_entity`:** el digest lo ve, y es el único
que puede.

```
  row_id=107 sku=SKU-NORMAL  updated_at ANTES=2026-09-10 19:58:53 DESPUES=2026-09-10 20:19:59  se movio=SI
  filas nuevas en la cola de cambios: NINGUNA

[OK] reconcile: {1: {…, 'digest_matches': True, 'content_matches': False,
                     'diverging_partitions': [{'partition': '49', 'magento_count': 1, 'mirror_count': 1,
                       'magento_digest': '2ce03779…ead4a95', 'mirror_digest': '9ec62794…6acfaffb'}]}, 3: {…}}

--- ¿lo repara delta_sync? NO: no hay fila de cola ---
[OK] delta_sync: {"changes_seen": 0, "records_updated": 0, "watermark": 10}
[OK] reconcile: … 'content_matches': False, 'diverging_partitions': [{'partition': '49', …}]
```

La divergencia **persiste** tras `delta_sync`, correctamente: no hay evento que
la cuente. Se repara releyendo la partición (o, hoy, con un `full_sync`, que es
lo que se hizo y dejó el espejo limpio otra vez). Ver §7 sobre por qué la
reparación dirigida no se construyó en esta tarea.

**C.2, `UPDATE` a una tabla de valores EAV:** el piso declarado. Nada lo
detecta.

```
  row_id=105 sku=sku-alpha-lower  updated_at ANTES=2026-09-05 08:30:00 DESPUES=2026-09-05 08:30:00  se movio=NO
  filas nuevas en la cola de cambios: NINGUNA

--- reconcile: NADA lo detecta ---
needs_full_sync': False
content_matches': True

--- y el espejo sigue con el valor VIEJO ---
       sku       |                        name
-----------------+----------------------------------------------------
 sku-alpha-lower | Alpha minusculas pisado por SQL directo 1789070182
   MySQL dice ahora: Alpha minusculas pisado por SQL directo 1789071655
```

Es el límite del sistema, con evidencia y no con una nota al pie. Cerrarlo
exigiría un digest sobre los valores EAV —más caro que re-sincronizar— o un
trigger en la base del cliente, que está fuera del contrato de sólo-lectura del
módulo.

### 5.4 Pasada completa e arnés de aceptación

Espejo recreado desde cero, instancia en Config B:

```
[OK] sync_attributes: {"pages_fetched": 3, "attributes_written": 1066, "options_written": 50535}
[OK] sync_categories: {"pages_fetched": 1, "categories_written": 6}
[OK] full_sync:       {"records_written": 16, "records_deleted": 0, "pages_fetched": 2,
                       "records_without_timestamp": 2, "skus_without_timestamp": ["SKU-Z-LAST","SKU-Z-LAST"]}
[OK] sync_signals:    {"store_views_read": 2, "signals_written": 5,
                       "signals_without_product_record": 0, "skus_without_product_record": []}
[OK] delta_sync:      {"changes_seen": 11, "records_updated": 8, "records_deleted": 0, "watermark": 11}
[OK] reconcile:       {1: {magento_count: 8, mirror_count: 8, digest_matches: True, needs_full_sync: False,
                           partition_count: 256, partitions_compared: 8, content_matches: True,
                           diverging_partitions: []},
                       3: {… idéntico …}}

[OK ] espejo_sincronizado: sin deriva
[OK ] score_por_store_view: conteos por store view: {1: 8, 3: 8}
[OK ] procedencia_de_scope: 16 registros revisados; procedencias: {'global': 115, 'store': 4, 'website': 3}
[OK ] identidad_de_opciones: 327 opciones con etiqueta distinta por store view reconocidas como una sola opción
[OK ] efecto_de_categoria: 6 producto(s) con efecto de categoría distinto entre store views; 9 par(es)
      evaluado(s), 0 sin evaluar por website_desconocido, 0 par(es) sin website de la tienda espejado
EXIT=0
```

El "sin deriva" del criterio 1 ahora **significa** algo más de lo que
significaba: `_espejo_sincronizado` reprueba también por contenido. El del
informe anterior era cierto sobre el conjunto y engañoso sobre lo que el
criterio afirma.

Las once filas de cola que `delta_sync` consumió son, precisamente, las que los
observers nuevos escribieron durante estos escenarios.

---

## 6. Las pruebas, y qué pasa si se borra el comportamiento

Cada prueba nueva se saboteó. Lo que se rompe:

| Sabotaje | Qué falla, y cómo |
|---|---|
| `SkuResolver` empareja por la clave y no por `entity_id` | `SkuResolverTest` × 2, nombrando el SKU equivocado (`SKU-DE-LA-FILA-1001`) |
| se quita una entrada de `etc/events.xml` | `EventWiringTest`, nombrando el evento que falta |
| se quita el `astimezone(UTC)` de `timestamp_token` | `test_the_token_is_the_utc_rendering_whatever_the_session_offset`; y, sobre la instancia real, 7 de 8 particiones divergen (§4.2) |
| `reconcile()` deja de comparar particiones | 4 pruebas de `test_reconcile.py` + la del arnés de aceptación |
| se reintroduce un filtro de versión propio | `NoOwnVersionFilterTest` y `PopulationMatchesMagentoTest` (ya existían) |
| `/checksums` deja de emitir `content_partitions` | `test_a_module_without_content_partitions_aborts_instead_of_reporting_no_drift`, del lado Python: `reconcile()` aborta en vez de decir "sin deriva" |

Guardas contra la vacuidad, porque una prueba de digest es fácil de escribir de
forma que no afirme nada:

- `ContentDigestTest` y `test_reconcile.py` comparten **constantes calculadas
  en el otro lenguaje**; no comparan una implementación consigo misma.
- `ChecksumReaderTest::testAChangedValueMovesTheContentDigestAndNotTheSkuDigest`
  afirma las dos mitades: que el `sku_digest` **no** se mueve (era el punto
  ciego) y que el de contenido sí, y **nombra** la partición esperada en vez de
  contar cuántas cambiaron.
- `test_the_digest_of_a_partition_does_not_depend_on_row_order` compara la
  lista con su inversa: los dos lados leen de bases distintas con órdenes
  distintos.
- `SkuResolverTest::testItAddsNoVersionPredicateOfItsOwn` exige que se haya
  ejecutado al menos una consulta antes de afirmar sobre sus `where`.
- La prueba de integración `testTheContentPartitionsCoverExactlyTheActive
  Population` recalcula los conteos por partición desde la población que un
  `Select` normal del framework considera activa, y exige `assertNotSame([])`
  para no pasar sobre un catálogo vacío.

Un efecto lateral que vale contar: al adoptar el helper `checksums_payload()`,
el criterio nuevo descubrió que **el propio arnés de aceptación se
contradecía** — su página de productos mockeada decía `updated_at` `10:00:00` y
su doble de `/checksums` construía el digest con `00:00:00`. Un doble que
responde "los mismos SKUs" con otro timestamp describe un espejo derivado. Los
dos usan ahora la misma constante.

---

## 7. Lo que NO se hizo, y por qué

1. **La reparación dirigida de una partición divergente.** `reconcile()`
   detecta y nombra; nadie repara todavía. La reparación es releer los SKUs de
   esa partición con `products_by_sku` y volver a hacer upsert — el mismo
   bucle que `delta_sync` ya tiene y que **no está factorizado** como función
   reutilizable. Factorizarlo y darle un punto de entrada es trabajo del lado
   del ingestor, que es literalmente H3 ("CLI de ingesta con commit por
   página"), explícitamente fuera de alcance de esta tarea. Hoy el remedio
   sigue siendo `full_sync`; la diferencia es que ahora se **sabe** cuándo hace
   falta y sobre qué, lo cual antes era invisible.

2. **Un digest sobre los valores EAV.** Cerraría el piso (§5.3 C.2) y cuesta
   más que re-sincronizar: 228.881 productos × 1.066 atributos en un endpoint
   cuya razón de ser es ser más barato que la alternativa. La otra forma de
   cerrarlo —un trigger en la base del cliente— rompe el contrato de
   sólo-lectura del módulo. Queda declarado como límite, con la medición que lo
   demuestra.

3. **Incluir `website_ids` o `category_ids` en el digest de contenido.** Habría
   cubierto el punto ciego de `updateWebsites()` sin depender del observer,
   pero exige leer `catalog_product_website` y `catalog_category_product`
   enteras en cada reconciliación y —peor— acordar una forma textual para una
   **lista de ids** entre los dos lados, incluido el caso `website_ids = NULL`
   ("desconocido") que el espejo admite a propósito. Es una superficie nueva
   para la misma clase de trampa de §4, a cambio de cubrir un camino que el
   observer ya cubre y que se verificó punta a punta (§5.2). Si algún día se
   agrega, el lugar es `ContentDigest` y la forma tiene que verificarse con el
   mismo procedimiento adversario del §4.2.

4. **Poda de `standard_skudo_change_log`.** Los observers nuevos hacen crecer
   la cola más rápido (una acción masiva sobre 20.000 productos escribe 20.000
   filas donde antes escribía cero). Es el hallazgo **M7**, que ya estaba
   abierto; esta tarea lo empeora en magnitud y no en naturaleza, y merece
   mención en `pendiente-s0.md` como consecuencia conocida.

5. **Recalcular los números de C2 contra el catálogo de producción.** Sigue
   pendiente del informe anterior. Esta vez **no se ejecutó ni un comando**
   contra la base `nisseicom` (el único intento, un `SELECT` de verificación al
   cierre, falló por falta de credenciales y no se insistió). La comprobación
   de que producción está intacta es la del sistema de archivos, §8.

6. **Un test de escala del digest.** El particionado se justifica con 228.881
   productos y se probó con 8. Que el payload sean 256 filas y no una por
   producto es la propiedad que importa y es estructural, pero nadie midió el
   coste de `fetchAll` de `(sku, updated_at)` sobre el catálogo entero. Es la
   misma laguna que H3 nombra para `full_sync` y le corresponde a esa tarea, que
   es la que va a tener el arnés de escala.

---

## 8. Confirmación de que producción no se tocó

```
$ find /var/www/casanissei.com -newermt '-8 hours' \( -type f -o -type d \)
(sin resultados)

$ ls -la /var/www/casanissei.com/v248/app/etc/env.php
-rwxrwxr-x 1 www-data www-data 5455 jul 21 14:23 .../app/etc/env.php
$ md5sum ...
c25797f9552cf4a6996f6131ef15473d
```

Mismo mtime y mismo md5 que registran los dos informes anteriores. Ni un
archivo ni un directorio bajo `/var/www/casanissei.com/` fue creado o
modificado. Cero `bin/magento` en `v248`, cero `exec` de escritura en
contenedores `*_local`, cero `INSERT`/`UPDATE`/`DELETE`/`ALTER`/`CREATE`/`DROP`
contra `nisseicom`. Toda escritura de esta tarea fue a
`/home/ingmar/magento-skudo-dev`, a la base `skudo_magento` del contenedor
`skudo_dev_db`, a la base `skudo_httpreal` de Postgres y a
`/home/ingmar/skudo-dev-logs/h1/`.

Del árbol de producción sólo se **leyó** (`sed`/`grep` sobre
`vendor/magento/module-catalog/...`, vía la copia de desarrollo, que es un
`rsync` de aquél).

---

## 9. Artefactos nuevos

Todos en `/home/ingmar/skudo-dev-logs/h1/`:

| Archivo | Qué |
|---|---|
| `updated-at-experiment.php` | el experimento del §1: cada camino de escritura, `updated_at` antes/después y las filas de cola |
| `import-experiment.php` | el camino de `CatalogImportExport`, con CSV y `Import::importSource()` de verdad |
| `updated-at-textual-agreement.py` | la verificación del §4: MySQL vs. `/products` vs. `/checksums` vs. el espejo, con la sesión de Postgres fuera de UTC |
| `scenarios.sh` | los tres escenarios del §5, punta a punta sobre HTTP real |
| `remove-website.php` | el escenario B con una BAJA de website (el caso que el digest no puede ver) |
| `probe-websites.php` | sonda que destapó el hueco de `sequence_product` (§1.4) |
