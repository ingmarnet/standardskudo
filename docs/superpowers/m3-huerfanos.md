# M3 — Filas huérfanas: qué se barre, qué se limpia por referencia, y qué no puede barrer una pasada a medias

Fecha: 2026-09-10. Cierre del hallazgo **M3** de `docs/superpowers/pendiente-s0.md`
—el último hueco de corrección del espejo mismo—: `delta_sync` borraba el
`ProductRecord` de un SKU eliminado y no sus asignaciones de categoría, el
`_sweep` de `full_sync` borraba sólo `ProductRecord`, y `sync_attributes` y
`sync_categories` documentaban "no hay barrido" como fuera de alcance.

**Por qué importa más que su tamaño.** Los detectores del eje 2 de S1
—"producto en una categoría en la que no debería estar", "sobre-categorizado",
"asignado a ninguna categoría"— leen `product_category_assignment`.
Alimentados con huérfanos, S1 emitiría hallazgos sobre productos que ya no
existen: el sistema **fabricando defectos** a partir de datos rancios, que es
lo que la sección 1 del spec nombra como el mayor riesgo del producto. Y en la
práctica, los primeros hallazgos que un humano viera serían falsos.

**Estado en una línea:** el espejo tiene ahora una invariante afirmada —después
de un ciclo de ingesta completo no queda NI UNA fila huérfana— sostenida por
tres mecanismos distintos elegidos por la naturaleza de cada tabla, y ninguno
de los barridos nuevos puede correr sobre una pasada interrumpida.

| | |
|---|---|
| Commits | `7745c0a` (asignaciones, clase 1), `58ee1a0` (opciones y categorías, clases 2 y 3), `39fca27` (la invariante del espejo, y las señales que encontró), `45db21b` (la reparación dirigida) |
| Suites | **313 pytest** (267 al empezar), **191 PHPUnit** sin cambios (+4 de integración en verde contra la instancia de desarrollo), `ruff` limpio |
| Migraciones | cadena Alembic en **0014** (`sync_pass` + el sello en cuatro tablas) |
| Módulo Magento | **sin cambios**: las tres clases se cierran del lado del ingestor |
| Verificación | HTTP real contra `127.0.0.1:8088`, espejo `skudo_httpreal`, con la pasada de **228.889 SKUs × 2 store views** de H3 reproducida |

---

## 1. El mecanismo elegido por clase, y por qué

Las tres clases no tienen la misma forma, y forzarlas al mismo mecanismo
habría roto una de ellas.

| Clase | Tabla(s) | Mecanismo | Puerta de pasada completa |
|---|---|---|---|
| 1 | `product_category_assignment` | **Referencial** | no la necesita, y no la tiene |
| 2 | `attribute`, `attribute_option`, `attribute_option_label` | **Sello de generación** | sí, `IncompletePassSweep` |
| 3 | `category`, `category_store_state` | **Sello de generación** | sí, `IncompletePassSweep` |
| (4) | `product_signal` | **Referencial** | no la necesita |

### Clase 1 — referencial, y por qué NO por sello

La asignación producto-categoría es **global**: en el core de Magento
`catalog_category_product` no tiene `store_id`, y el espejo lo respeta
(`ProductCategoryAssignment` no tiene store view). La pasada completa, en
cambio, es **por store view**.

Un sello de generación sobre esa tabla habría sellado la misma fila una vez por
tienda, y sobre una pasada de dos tiendas interrumpida a mitad de la primera
habría dejado sin sellar asignaciones perfectamente vivas. Peor: el barrido
tendría que decidir CUÁNDO una fila global "ya fue vista", con dos pasadas por
tienda escribiéndola.

El predicado referencial no tiene ninguno de esos problemas:

```python
# mirror/categories.py
has_record = select(ProductRecord.id).where(
    ProductRecord.tenant_id == tenant_id,
    ProductRecord.sku == ProductCategoryAssignment.sku,
).exists()
delete(ProductCategoryAssignment).where(
    ProductCategoryAssignment.tenant_id == tenant_id, ~has_record
)
```

"No hay `product_record` de este tenant con este sku" es verdadero o falso con
independencia de qué pasada escribió la fila y de si alguna terminó. Por eso
**no lleva la puerta de `IncompletePassSweep` y no la necesita**: por
construcción no puede borrar la asignación de un producto que el espejo
contiene. Es equivalente al sello en lo que el sello garantizaba, y estrictamente
más seguro en el caso que al sello se le escapaba.

Dos detalles que cargan peso:

- El `EXISTS` pregunta por **cualquier** store view del tenant. Un producto que
  el origen retiró de PY pero sigue ofreciendo en BR conserva su fila de BR y su
  asignación sigue siendo verdadera. Mirar sólo la store view en curso dejaría
  al producto de BR sin categorías a mitad de un `full_sync` de dos tiendas —y
  el detector "sin categoría" de S1 lo reportaría en masa. Es la prueba
  `test_an_assignment_of_a_product_still_mirrored_in_one_store_view_survives`.
- Corre **después** del recorrido de todas las store views, no dentro del bucle,
  y por eso una interrupción no llega nunca a ejecutarlo: el fallo benigno.

En `delta_sync` la limpieza **no** es referencial sino por **lista explícita de
SKUs**, en la misma transacción que el borrado del registro:

```python
delete(ProductCategoryAssignment).where(
    ProductCategoryAssignment.tenant_id == tenant_id,
    ProductCategoryAssignment.sku.in_(to_delete),
)
```

Ahí se sabe exactamente qué SKUs se borraron, y un borrado acotado a esa lista
no puede tocar las categorías de un producto vivo. Dejarlo para un barrido
posterior habría dejado una ventana en la que el espejo tiene asignaciones de un
producto sin registro — que es exactamente el estado que S1 no debe leer.

### Clases 2 y 3 — sello de generación, con la puerta de H3

Acá el predicado referencial no sirve: una opción que el origen borró **no deja
de tener atributo**, y una categoría que el origen borró no deja de existir por
sí sola. Lo único que distingue una fila viva de una muerta es que la última
pasada la haya visto. Eso es exactamente lo que H3 ya resolvió para los
productos, y lo que M3 generaliza.

- Migración **0014**: `sync_generation` (BigInt, default 0) en `attribute`,
  `attribute_option`, `category` y `category_store_state`, y la tabla
  `sync_pass` —una fila por (tenant, tipo de pasada)— como equivalente de
  `full_sync_checkpoint` para las pasadas que no son por store view.
- `src/skudo/ingest/sweep.py` concentra la maquinaria: la secuencia del sello,
  `start_pass`, `note_page`, `require_complete_pass` y `IncompletePassSweep`,
  que pasa a ser **una sola excepción para los tres barridos** (estaba en
  `full_sync`; dos tipos para el mismo modo de fallo invitarían a tratarlos
  como problemas distintos).
- `sync_attributes` y `sync_categories` confirman **por página** con el sello en
  la misma transacción, y barren al terminar.

Tres decisiones dentro de esto:

1. **`attribute_option_label` no lleva sello propio.** `upsert_option` reemplaza
   el juego completo de etiquetas de la opción en cada pasada, así que una
   etiqueta sólo puede quedar huérfana si su OPCIÓN desaparece; el barrido de
   opciones las borra primero (la FK no tiene cascada). Un sello propio sería un
   segundo criterio para la misma decisión.
2. **`upsert_option` pasó de `on_conflict_do_nothing` a `do_update` del sello.**
   Con `do_nothing`, una opción que ya existía y que la pasada volvió a ver
   conservaría el sello viejo y el barrido de su propia pasada se la llevaría.
   Es el bug que el cambio de mecanismo introduce si no se mira.
3. **El estado por tienda se sella aparte de su categoría.** Una store view
   retirada de la instancia deja la categoría viva y su estado huérfano, y
   `derive_category_effect` leería un `is_active` de una tienda que no existe.
   Con el sello de la categoría como criterio, ese caso no se vería.

Y una asimetría deliberada con `full_sync`: **estas pasadas no se reanudan**.
`sync_pass` no tiene `next_cursor` a propósito. Una pasada interrumpida se
repite completa con una generación nueva, y eso es seguro precisamente porque
recorre desde la primera página: todo lo que el origen sigue ofreciendo se
vuelve a sellar. El cursor persistido que `full_sync` necesita —releer 228.889
productos cuesta media hora— acá no tendría lector: son unos miles de filas y la
pasada entera tarda **68 s** contra la instancia de desarrollo.

### El sello es obligatorio, no opcional

`upsert_attribute`, `upsert_option`, `upsert_category` y
`set_category_store_state` reciben `sync_generation` como **keyword obligatorio,
sin default**. Un default silencioso —0, o la generación anterior— haría que un
llamador que se olvide de pasarlo escriba filas que el barrido de su propia
pasada borra a continuación. Se prefiere que no compile.

---

## 2. Cómo se probó que una pasada interrumpida no puede barrer

Tres capas, igual que en H3, y las tres fallan si el mecanismo se relaja.

**Por qué la exigencia es mayor que para los productos:** un producto barrido de
más vuelve en la próxima sincronización. Una **opción** barrida de más se lleva
sus etiquetas, y esas etiquetas son el único registro de que "Negro" y "Preto"
son la misma `option_id` — la identidad que este sub-proyecto existe para
proteger, y sobre la que decide la consolidación de S1.

### 1. La puerta vive dentro del barrido y se lee de la base

```python
# ingest/sweep.py
def require_complete_pass(session, tenant_id, pass_kind, generation) -> SyncPass:
    row = get_pass(session, tenant_id, pass_kind)
    if row is None or row.generation != generation:
        raise IncompletePassSweep(...)   # ninguna fila lleva ese sello: barrería TODO
    if not row.pass_complete:
        raise IncompletePassSweep(...)   # barrería lo que falta por recorrer
    return row
```

`_sweep_attributes` y `_sweep_categories` la llaman como primera línea, así que
un barrido sobre una pasada a medias **no es expresable**: no es una llamada que
haya que acordarse de no hacer.

### 2. Las pruebas (`tests/ingest/test_catalog_sweeps.py`, 15 casos)

Por cada una de las dos pasadas:

- **Sincronización deliberadamente interrumpida.** Un transporte que estalla
  antes de la segunda página, con la página 1 ya sin nombrar al atributo/la
  categoría que se quiere proteger: si el barrido corriera, se lo llevaría. Se
  afirma que la opción, sus **etiquetas**, el atributo y la fila de `sync_pass`
  (`pass_complete=false, swept=false`) siguen ahí.
- **El barrido llamado a mano sobre la pasada a medias** falla con
  `IncompletePassSweep` y no borra nada.
- **El barrido llamado con una generación ajena** —el caso en que se llevaría
  TODAS las filas del tenant, porque ninguna lleva ese sello— falla igual.
- Y la contraparte, para que la puerta no se vuelva "no barrer nunca": una
  pasada que **sí** termina barre lo que el origen dejó de ofrecer, y la opción
  que sigue viva conserva sus dos etiquetas.

### 3. Sobre HTTP real, matando el proceso

No una excepción inyectada: `SIGKILL` a mitad de la pasada de atributos contra
la instancia de desarrollo, con 50.535 opciones y una opción rancia sembrada a
mano en el espejo que el origen no ofrece.

```
$ psql -c "insert into attribute_option (…, magento_option_id, sync_generation)
           values (…, 987654321, 0)"                     -- una opción que el origen NO ofrece
$ psql -c "insert into attribute_option_label (…) values (…, 'RANCIA-M3')"

$ timeout --signal=KILL 55 python -m skudo.cli attributes --tenant skudodev
Terminado (killed)   EXIT=137

 pass_kind  | generation | pages_done | items_written | pass_complete | swept
------------+------------+------------+---------------+---------------+-------
 attributes |         20 |          1 |           500 | f             | f     <-- media pasada
 categories |          8 |          1 |             6 | t             | t

 rancia_sigue | etiquetas_de_la_rancia | opciones_totales
--------------+------------------------+------------------
            1 |                      1 |            50536   <-- el barrido NO corrió
```

La página 1 (500 atributos) **sí** quedó confirmada —commit por página— y el
sello dice `pass_complete: false`, así que el barrido no es expresable. La
opción rancia, que un barrido indebido se habría llevado con su etiqueta, sigue
en pie. Y la invocación siguiente, sin argumentos nuevos, **sí** barre:

```
$ python -m skudo.cli attributes --tenant skudodev
{ "generation": 21, "pages_fetched": 3, "attributes_written": 1066,
  "options_written": 50535, "attributes_deleted": 0,
  "options_deleted": 1, "option_labels_deleted": 1 }

 rancia_sigue | opciones_totales      pass_kind  | pass_complete | swept
--------------+------------------    ------------+---------------+-------
            0 |            50535     attributes  | t             | t
```

Una opción de 50.536 y su etiqueta, no las 50.535 restantes.
(`/home/ingmar/skudo-dev-logs/m3/interrupted-attributes-real.log`)

---

## 3. Cómo se probó que cada borrado está acotado por tenant

Un barrido es un `DELETE` sobre tablas compartidas por todos los tenants: es el
único lugar del sistema donde un filtro `tenant_id` ausente no sería meramente
incorrecto sino **catastrófico** —borraría el catálogo de otro cliente—. Cada
borrado nuevo tiene su prueba, y ninguna es de la forma débil ("el otro tenant
está vacío"), que seguiría pasando con el filtro quitado.

| Borrado | Prueba de aislamiento | Qué pasaría sin el filtro |
|---|---|---|
| `delete_orphan_category_assignments` (delete) | `test_cleaning_one_tenant_never_deletes_another_tenants_assignments`: el MISMO sku huérfano sembrado en los dos tenants | se lleva las asignaciones del otro cliente |
| `delete_orphan_category_assignments` (exists) | `test_a_record_of_another_tenant_does_not_make_an_orphan_look_alive` | el producto del otro tenant con el mismo sku salvaría la fila rancia **para siempre** |
| `delete_orphan_signals` (delete y exists) | dos pruebas simétricas en `tests/mirror/test_signals.py` | ídem, sobre señales |
| `delta_sync` (asignaciones y señales) | `test_a_delete_in_one_tenant_leaves_the_other_tenants_assignments_alone` | el borrado de un SKU en un tenant borra el del otro |
| `full_sync` (limpieza final) | `test_a_full_sync_of_one_tenant_leaves_another_tenants_assignments_alone`, con una huérfana **igualmente candidata** en el otro tenant | ídem |
| `_sweep_attributes` | `test_the_attribute_sweep_never_touches_another_tenants_rows`: las filas del otro tenant se **desellan a mano** (generación 0) para volverlas candidatas perfectas | se lleva atributos, opciones y etiquetas del otro cliente |
| `_sweep_categories` | `test_the_category_sweep_never_touches_another_tenants_rows`, igual | ídem |

El caso especial es `attribute_option_label`: **no tiene `tenant_id` propio**,
su alcance es su opción. El barrido las acota por `option_row_id IN (opciones de
este tenant)`, así que ese subselect es todo el aislamiento que hay — y por eso
la prueba afirma explícitamente que las 4 etiquetas del otro tenant siguen ahí,
contando también el total global de la tabla.

Y por encima de las pruebas por borrado, una del espejo entero:
`test_the_cycle_of_one_tenant_leaves_the_other_tenants_mirror_intact` corre el
ciclo completo de un tenant y compara los **siete conteos** del espejo del otro
antes y después.

---

## 4. La invariante: el espejo entero, no una función

`tests/mirror/test_mirror_has_no_orphans.py`.

Este sub-proyecto ya envió **tres veces** un hueco que una prueba por función no
podía ver: cuatro tablas cuyos `upsert_*` sólo se llamaban desde tests (C1),
`product_signal` en la misma fase que arreglaba esas cuatro (C3), y ahora M3.
En los tres casos cada función hacía exactamente lo que su prueba decía. Lo que
faltaba era alguien que mirara el espejo ENTERO y preguntara si sus filas se
sostienen unas a otras.

**Las aristas vigiladas** (nombre → tabla hija → consulta):

| Arista | Clase |
|---|---|
| asignación sin `product_record` | M3, clase 1 |
| señal sin `product_record` | fuera de las tres, ver §5 |
| opción sin atributo | M3, clase 2 |
| etiqueta sin opción | M3, clase 2 |
| estado por tienda sin categoría | M3, clase 3 |
| asignación a una categoría no espejada | declarada, sin limpieza, ver §6 |

**Tres capas:**

1. `test_a_complete_ingest_cycle_leaves_no_orphans` corre el ciclo completo
   contra una instancia falsa cuyo catálogo **cambia a mitad**: atributos,
   categorías, pasada completa, señales, un delta que BORRA un producto, y las
   segundas pasadas de atributos y categorías que barren lo que el origen dejó
   de ofrecer. Cero huérfanas en las seis aristas.
   Con su contraparte obligatoria, `test_the_cycle_really_removed_things`: la
   invariante se cumple trivialmente sobre un espejo que nunca borró nada, así
   que se exige que el ciclo haya ejercido los tres barridos (7 conteos exactos).
2. `test_the_invariant_detects_an_orphan_of_every_class`, parametrizada: se
   siembra a mano un huérfano de cada clase —saltándose los caminos de ingesta,
   que es precisamente lo que hace un camino futuro con un hueco— y se exige que
   el detector lo nombre. Una invariante que no sabe fallar no afirma nada.
   La etiqueta sin opción es la excepción y tiene su propia prueba: la FK de la
   base la hace **insembrable**, así que ese modo de fallo no es una huérfana
   sino un error de integridad —que es lo que pasaría si el barrido de opciones
   se olvidara de borrar las etiquetas primero—.
3. `test_every_mirror_table_declares_its_referential_status`: toda tabla del
   espejo tiene que declararse **raíz** o **hija de una arista vigilada**. Una
   tabla nueva no puede entrar sin que alguien decida si sus filas pueden quedar
   huérfanas. `CHILD_TABLES` se **deriva** de las aristas, no es una lista
   paralela que pueda desincronizarse.

Las consultas de la invariante se escriben con `LEFT JOIN ... IS NULL`, no con
el `NOT EXISTS` que usa la limpieza del producto: si compartieran predicado, un
error en el predicado los dejaría pasar a los dos a la vez.

**Qué atraparía.** Un camino de escritura futuro que borre un producto y se
olvide de sus asignaciones; un barrido nuevo que borre una categoría y deje sus
estados; una tabla nueva que nadie declaró; y —lo que ya atrapó— una tabla
existente en la que nadie había pensado.

---

## 5. Lo que la invariante encontró a la primera: las señales

Escrita la invariante, el ciclo completo falló con **una** arista rota que no
estaba entre las tres clases de M3: `product_signal` del producto borrado.

Se cerró igual, con la misma forma referencial que las asignaciones
(`delete_orphan_signals` al final de la pasada completa; por lista explícita en
el borrado de `delta_sync`), porque el impacto es **mayor** que el de una
asignación huérfana: **S1 prioriza los hallazgos por señal comercial**. Un SKU
fantasma con facturación no sería un hallazgo fabricado más — sería el
**primero** que un humano ve, que es exactamente el fallo que este cierre existe
para evitar.

No contradice el "no hay barrido" que `sync_signals` declara y que se mantiene:
aquello es sobre un SKU que deja de **vender** —su última medición sigue siendo
el último hecho observado, fechado, y decidir cuándo caduca es del consumidor—;
esto es sobre un SKU que deja de **existir**, y de él no hay nada que priorizar.
Son dos preguntas distintas, y el docstring de `sync_signals` ahora lo dice.
Tampoco esconde el hallazgo A1: `sync_signals` cuenta y NOMBRA los SKUs con
señal sin registro cuando escribe, mucho antes de que esta limpieza corra.

---

## 6. Lo que NO se hizo, con la razón

**1. Asignaciones a una categoría que el espejo no tiene: vigilada, sin
limpieza.** La tabla `category` la puebla una pasada **independiente** de la de
productos. Tratar "categoría desconocida" como huérfana borraría la tabla entera
de asignaciones de un tenant que todavía no corrió `skudo categories` — el
colapso de *desconocido* en *incorrecto* que la sección 1 del spec nombra como el
mayor riesgo del sistema. En un ciclo completo la arista se sostiene sola (el
payload de `/products` es la verdad de las dos mitades), así que la invariante la
**exige igual**: si un camino futuro rompiera esa coherencia, se ve ahí. Cerrarla
de verdad necesita condicionarla a una pasada de categorías completa, que es
trabajo del eje 2 (L5) y no de este cierre.

**2. Sin índice sobre `sync_generation` en las cuatro tablas nuevas**, por la
misma razón que H3 no lo puso en `product_record`: el barrido no es el cuello de
botella. Medido en §7.

**3. El módulo Magento no cambió.** Las tres clases se cierran del lado del
ingestor porque el origen ya dice la verdad completa en cada pasada: lo que no
aparece, no está. Un endpoint de "borrados" habría sido una segunda definición
de la misma cosa.

**4. `attribute_set` sigue sin camino de ingesta** (nadie la escribe, ni antes
ni ahora). Está declarada como raíz en la invariante, no como hija, porque lo que
le falta es un ingestor y no un barrido. Es un hallazgo distinto, anterior a M3.

**5. ~~Una pasada COMPLETA que no devuelve nada barre todo.~~ CERRADO** en la
segunda ronda — ver §9.

**6. La reparación dirigida SÍ se tocó** (commit `45db21b`): borraba
`ProductRecord` de los SKUs que la partición ya no contiene y dejaba sus
asignaciones y señales hasta la próxima pasada completa. Se cierra con el mismo
predicado referencial ACOTADO a la cohorte reparada, porque la lista de SKUs no
alcanza como criterio: la reparación es por store view y la asignación es
global.

---

## 7. La escala: 457.762 → 0

Se reprodujo el experimento de H3 contra la instancia de desarrollo
(`127.0.0.1:8088`, espejo `skudo_httpreal`), que es donde el hallazgo se midió:
228.881 productos sintéticos sembrados, la pasada completa, y después el origen
los retira y la pasada siguiente barre.

### La pasada que puebla

```
$ skudo full-sync --tenant skudodev --page-size 1000 --restart
{ "generation": 9, "records_written": 457778, "records_deleted": 0,
  "category_assignments_deleted": 0, "signals_deleted": 0, "pages_fetched": 458 }
Elapsed (wall clock): 34:19        Maximum resident set size: 99 MB
```

457.778 registros y 457.771 asignaciones, sin borrar nada: el origen sigue
ofreciendo todo, así que la limpieza no tiene nada que hacer. El coste y la
memoria son los de H3 (33,6 min, 98 MB): **la limpieza no cambió el perfil de
una pasada que no borra**.

### La pasada que barre

Con los 228.881 productos retirados de la instancia, el origen vuelve a ofrecer
8 SKUs por store view:

```
########## ANTES
 asignaciones_sin_producto | senales_sin_producto | opciones_sin_atributo | etiquetas_sin_opcion | estados_sin_categoria
             0             |          0           |           0           |          0           |           0
 registros | asignaciones | opciones | etiquetas | categorias | estados
    457778 |       457771 |    50535 |     58576 |          6 |      12

$ skudo full-sync --tenant skudodev --page-size 1000 --restart
{ "generation": 10, "records_written": 16, "records_deleted": 457762,
  "category_assignments_deleted": 457762, "signals_deleted": 0, "pages_fetched": 2 }
Elapsed (wall clock): 0:16.67      Maximum resident set size: 192 MB

########## DESPUÉS
 asignaciones_sin_producto | senales_sin_producto | opciones_sin_atributo | etiquetas_sin_opcion | estados_sin_categoria
             0             |          0           |           0           |          0           |           0
 registros | asignaciones | opciones | etiquetas | categorias | estados
        16 |            9 |    50535 |     58576 |          6 |      12
```

**`category_assignments_deleted: 457762` es exactamente el número que H3 midió
como filas que quedaban en pie** (`docs/superpowers/h3-cli-y-escala.md`, §5): la
misma pasada, la misma instancia, la misma escala, y ahora la limpieza se las
lleva en la misma invocación. El conteo de huérfanas después es **0** en las
cinco clases.

Las 9 asignaciones y los 16 registros que quedan son el catálogo real de la
instancia de desarrollo, intacto: un barrido demasiado amplio habría dejado 0 y
habría pasado igual un "¿quedó limpio?".

### Lo que costó

| | H3 (sin la limpieza) | M3 (con ella) |
|---|---|---|
| Pasada que barre 457.762 registros | 13,2 s | **16,7 s** |
| RSS pico | 74 MB | 192 MB |

**~3,5 s** para borrar 457.762 asignaciones por el predicado referencial, sobre
una pasada de 16,7 s: no es el cuello de botella, y por eso no se agregó un
índice. El plan es un `Hash Anti Join` entre las dos tablas (verificado con
`EXPLAIN`), lineal en el tamaño del espejo; el pico de RSS es el del lado
Postgres del cliente, no del ingestor.

### Clases 2 y 3, sobre HTTP real y a la escala de su tabla

La pasada de atributos de esta instancia son **1.066 atributos y 50.535
opciones con 58.576 etiquetas**, en 3 páginas y **68 s**. Se creó una opción
sintética en el Magento de desarrollo con tres etiquetas (admin, PY, BR), se
sincronizó, se borró del origen y se volvió a sincronizar:

```
opción creada  -> options_written: 50536,  opciones/etiquetas del espejo: 50536 / 58579
opción borrada -> options_deleted: 1, option_labels_deleted: 3
                  opciones/etiquetas del espejo: 50535 / 58576
huérfanas: opciones_sin_atributo = 0, etiquetas_sin_opcion = 0
```

Una opción de 50.536 y **exactamente** sus tres etiquetas.
(`/home/ingmar/skudo-dev-logs/m3/option-lifecycle.log`)

**Lo que esa medición dejó ver, y queda declarado:** la pasada de atributos
escribe **una sentencia por opción** (`upsert_option` reemplaza el juego de
etiquetas de cada opción por separado), que es lo que hace que 50.535 opciones
cuesten 68 s y que la primera página tarde ~50 s. Es el mismo defecto que H3
midió y arregló para los productos (compilar el SQL, no Postgres). No se tocó
acá: cambiar la forma de escritura de las etiquetas en el mismo cierre que
introduce su barrido habría mezclado dos cambios de riesgo distinto sobre la
tabla que guarda la identidad de las opciones. Queda anotado en
`pendiente-s0.md`.

### El estado final de la instancia

La instancia de desarrollo quedó como estaba (10 filas de entidad, 8 activas, y
sin la opción sintética: la FK de `eav_attribute_option_value` la borró en
cascada). Sobre ese espejo:

```
$ skudo reconcile --tenant skudodev     # exit 0, content_matches en las dos tiendas
$ skudo accept --tenant skudodev        # los CINCO criterios en OK
[OK ] espejo_sincronizado / score_por_store_view / procedencia_de_scope /
      identidad_de_opciones / efecto_de_categoria
$ skudo status --tenant skudodev
  "catalog_passes": [
    {"pass_kind": "attributes", "generation": 21, "pages_done": 3,
     "items_written": 1066, "pass_complete": true, "swept": true},
    {"pass_kind": "categories", "generation": 22, "pages_done": 1,
     "items_written": 6, "pass_complete": true, "swept": true}]
```

Y la suite de integración PHP, que se salta sola por encima de 5.000 productos
activos, volvió a correr en verde: `4 tests, 27 assertions`.



---

## 8. Cambios en el esquema y en la superficie

- Migración **0014**: tabla `sync_pass`; columna `sync_generation` en
  `attribute`, `attribute_option`, `category`, `category_store_state`.
- `skudo status` publica `catalog_passes`: el tipo, la generación, las páginas y
  los sellos `pass_complete`/`swept` de las dos pasadas. Una con
  `pass_complete: false` es una pasada que se cortó y que no barrió nada.
- Reportes: `full_sync` agrega `category_assignments_deleted` y
  `signals_deleted`; `delta_sync`, los mismos dos; `sync_attributes` agrega
  `generation`, `attributes_deleted`, `options_deleted`,
  `option_labels_deleted`; `sync_categories`, `generation`,
  `categories_deleted` y `category_store_states_deleted`.
- Sin cambios en el módulo PHP.


---

## 9. Segunda ronda: la válvula del barrido masivo y el lote de opciones

Dos cosas que el cierre de M3 dejó anotadas y la revisión pidió cerrar.
Commits `13a4b26` (válvula) y `0d2fa2f` (lote). Suites: **333 pytest**, 191
PHPUnit + 4 de integración, `ruff` limpio.

### 9.1 La válvula: un barrido que se llevaría la mayoría se NIEGA

**El hueco.** Para el sello, una pasada completa que no devolvió NADA es
indistinguible de "el origen dejó de ofrecer todo el catálogo": la pasada
termina, marca `pass_complete`, y el barrido borra el espejo entero del
tenant. Basta un endpoint que responda `[]` por un bug, un token vencido que
igual devuelva 200, o una paginación rota. El espejo es derivado y una
resincronización lo reconstruye, así que el daño está acotado — pero es
**silencioso**, y un sistema cuya premisa es no destruir valor no puede tener
un camino destructivo que dispara con más fuerza justo cuando algo aguas
arriba se rompió.

`guard_mass_sweep` (en `ingest/sweep.py`) se llama DENTRO de los tres barridos
y **antes de la primera sentencia de borrado**, así que una negativa deja el
espejo exactamente como estaba. Aborta con `MassSweepRefused`, los conteos y
la bandera en el mensaje.

**El umbral: la mayoría estricta, 0,5.** Por debajo de la mitad, un borrado
grande es indistinguible de la rotación normal de un catálogo, y una válvula
que dispara en las pasadas normales es una válvula que alguien apaga. Por
encima de la mitad, el barrido está afirmando algo sobre el catálogo ENTERO
del tenant, y eso merece un humano. El fallo que motiva todo esto produce
siempre el 100 %, cómodamente del lado que se niega. La comparación es
**estricta**: exactamente la mitad pasa, porque "la mayoría se va" y "la mitad
cambió" son cosas distintas.

**El piso: 10 filas.** Por debajo, la válvula no se aplica. No es una
concesión: ahí no puede proteger nada que importe —resincronizar un puñado de
filas cuesta segundos— y a cambio obligaría a cualquier espejo recién nacido a
llevar bandera.

**Dónde se mide.** Sobre lo que ESE barrido alcanza: esa store view, esa tabla.
Medirlo sobre el espejo entero haría que vaciar PY con BR intacto diera 50 % y
pasara inadvertido — hay una prueba para eso. Atributos y categorías miden sus
DOS tablas selladas por separado, para que 50.000 opciones muertas no se
escondan detrás de 1.000 atributos vivos.

**La salida es una bandera, no un dial.** `--sweep-anyway` en `full-sync`,
`attributes` y `categories`. Un umbral configurable en un cron termina
configurado en 1,0 y la válvula deja de existir sin que nadie lo decida; una
bandera hay que escribirla cada vez.

**El estado que deja la negativa** es `pass_complete=true, swept=false`: ni a
medio barrer ni marcada como completa-y-barrida. La invocación siguiente
**continúa la misma generación** —no relee el catálogo— y vuelve a negarse
hasta que alguien autorice. Hay una prueba que lo afirma sobre el checkpoint y
sobre la generación.

**Las pruebas, y la comparación invertida.** 16 casos en
`tests/ingest/test_mass_sweep_valve.py`, y cada barrido tiene las DOS que
discriminan:

- una pasada normal que borra **11 de 41** —por encima del piso, así que la
  válvula sí se consulta, y por debajo de la mayoría— tiene que barrer **sin**
  bandera;
- una que borraría todo tiene que **negarse** sin bandera y barrer con ella.

El 11 no es decorativo: con el 1 de 31 que tenía la primera versión de estas
pruebas, el piso corta antes que la comparación y una inversión pasaría
inadvertida. Comprobado por sabotaje:

| Sabotaje | Resultado |
|---|---|
| `<=` invertido a `>=` en la comparación de share | **12 de 16 fallan**, incluidas las tres de pasada normal |
| válvula desactivada (`if True: return`) | **9 de 16 fallan**, las de negativa |

**Sobre HTTP real.** Se sembraron en el espejo 60.000 opciones que el origen no
ofrece; la pasada siguiente sella las 50.535 de verdad y el barrido se llevaría
60.000 de 110.535:

```
$ skudo attributes --tenant skudodev
MassSweepRefused: el barrido borraría 60000 de 110535 fila(s) de
attribute_option (54.3%), más de la mayoría. Se aborta SIN tocar el espejo […]
Verificá el origen; si el vaciado es real, repetí con --sweep-anyway.
EXIT=1

 opciones_intactas | pass_kind  | generation | pass_complete | swept
        110535     | attributes |         37 | t             | f

$ skudo attributes --tenant skudodev --sweep-anyway
{ "options_written": 50535, "options_deleted": 60000 }   EXIT=0
 opciones: 50535     etiquetas: 58576
```

(`/home/ingmar/skudo-dev-logs/m3/valve-real.log`)

### 9.2 El lote de opciones, y el techo duro que la medición encontró

La pasada de atributos escribía **cuatro sentencias por opción** —insert,
select del id, delete de las etiquetas, insert de las etiquetas— y 50.535
opciones costaban 68 s. Mismo defecto y mismo arreglo que H3 para los
productos: el coste dominante era compilar el SQL, no Postgres.

`upsert_options` escribe un lote en **tres** sentencias. El insert va con
`ON CONFLICT DO UPDATE … RETURNING`, y no puede ser `DO NOTHING` por dos
razones que se juntan: una opción que ya existía y que la pasada volvió a ver
necesita el sello nuevo —si no, el barrido de su propia pasada se la lleva— y
`DO NOTHING` no devuelve fila en el conflicto, así que el `RETURNING` que
reemplaza al select por opción se quedaría sin los ids de todo lo que ya
existía. El envoltorio de a una opción se mudó a `tests/skudo_testing.py`
delegando en el lote, igual que `upsert_record` tras H3.

**El defecto que ninguna prueba veía.** La primera versión escribía la PÁGINA
entera en una sentencia, y la página grande de este catálogo —~17.000
opciones— revienta: el protocolo de Postgres admite **65.535 parámetros** por
sentencia y el insert manda 4 por fila, así que por encima de **16.383**
opciones falla con `number of parameters must be between 0 and 65535`. En la
suite no fallaba —los lotes son de dos opciones—: fallaba contra el catálogo
real, a mitad de una pasada. Lo encontró la medición, no una prueba, que es
justo el patrón que este proyecto ya pagó tres veces.

Ahora se trocea en lotes de **2.000** (factor de ocho de margen) y hay dos
pruebas: una que escribe el doble del límite en una llamada —falla sin el
troceo— y otra que afirma el margen en aritmética, para que subir la constante
falle antes de que lo haga una pasada.

**Antes y después**, tres corridas contra la instancia de desarrollo sobre
HTTP real, verificando en cada una que la pasada devolvió sus 50.535 opciones:

| | antes | después |
|---|---|---|
| 1.066 atributos, 50.535 opciones, 58.576 etiquetas, 3 páginas | **68,4 s** | **19,9 s** (20,2 / 19,6 / 19,8) |
| RSS pico | 105 MB | 136 MB |

3,4× más rápido. El troceo de 2.000 y el de 10.000 dan el **mismo** tiempo y
el segundo cuesta 190 MB de RSS, así que el lote chico no compra velocidad a
cambio de memoria: la cota de memoria por construcción es la misma propiedad
que `upsert_records` sostiene para los productos.

Tras las dos rondas, el espejo de desarrollo sigue en **cero huérfanas** en las
cinco clases y `skudo accept` sigue dando los cinco criterios en OK.
