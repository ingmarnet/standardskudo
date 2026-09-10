# H3 — Punto de entrada, escala y poda de la cola

Fecha: 2026-09-10. Cierre del hallazgo **H3** de `docs/superpowers/pendiente-s0.md`
—el sub-proyecto no tenía forma de poblar el espejo que su propio criterio de
aceptación mide— más los dos puntos que el cierre de H1 dejó abiertos: la
**reparación dirigida** de una partición divergente y la **poda** de la cola de
cambios (**M7**).

**Estado en una línea:** hay un CLI con once comandos; la pasada completa
confirma por página y se reanuda sin poder barrer lo que no vio; una partición
divergente se repara releyendo sólo sus SKUs; la cola de cambios se poda sin
poder borrar una fila que el ingestor no leyó; y la escala está medida, no
supuesta.

| | |
|---|---|
| Commits | `8f5dea3` (pasada reanudable), `a3062d2` (reparación dirigida), `043c292` (CLI), `fdda4be` (poda de la cola, M7), `eba6c63` (escritura por lote + test de escala) |
| Suites | **267 pytest**, **191 PHPUnit** (187 unitarias + 4 de integración), `ruff` limpio |
| Migraciones | cadena Alembic en **0013** (`full_sync_checkpoint`); esquema del módulo con `standard_skudo_delta_read` |
| Verificación | HTTP real contra `127.0.0.1:8088`, espejo `skudo_httpreal`, y una pasada de **228.889 SKUs × 2 store views** sembrada en la instancia de desarrollo |

---

## 1. El CLI: comandos y códigos de salida

`python -m skudo.cli <comando>` (o `skudo`, con el paquete instalado). Antes de
esto `main()` existía SOLO en `src/skudo/acceptance/s0.py`: no había alta de
tenants ni forma de correr una ingesta, así que el comando de aceptación
presuponía un espejo poblado que nada permitía poblar, y el primer criterio
—"espejo de 200k SKUs × 2 store views sincronizado"— no se podía ni intentar.
`sync_signals`, `sync_categories` y `sync_attributes` eran alcanzables sólo
desde los tests.

| Comando | Qué hace |
|---|---|
| `register-tenant --code --base-url [--name] [--token-env-var]` | Da de alta o **actualiza** un tenant. `--token-env-var` es el NOMBRE de la variable de entorno donde vive el token (por defecto `SKUDO_TENANT_<CÓDIGO>_TOKEN`), nunca el token. |
| `probe --tenant` | Sonda el Magento del tenant y espeja su topología (websites, grupos, store views) más el snapshot de entorno. |
| `full-sync --tenant [--stores] [--page-size] [--restart]` | Carga completa, confirmando por página y **reanudable**. |
| `delta-sync --tenant [--stores]` | Aplica los cambios pendientes de la cola y las activaciones de versión. |
| `attributes --tenant` | Atributos, opciones y etiquetas por store view. Va **antes** de `full-sync`. |
| `categories --tenant [--stores]` | Categorías, su `path` y su estado por tienda. |
| `signals --tenant [--stores] [--days]` | Señales comerciales por store view. |
| `reconcile --tenant [--stores]` | Compara el espejo con Magento (conjunto **y** contenido por partición). |
| `repair --tenant --store (--partitions ab,cd \| --from-reconcile)` | Relee sólo los SKUs de esas particiones. |
| `status --tenant` | Estado del espejo, del watermark, de la pasada en curso y del entorno. |
| `accept --tenant [--stores]` | Los cinco criterios de aceptación de S0. |

Sin `--stores`, los comandos que lo aceptan usan las store views que la sonda
ya espejó para ESE tenant. No es una comodidad: el barrido de la pasada
completa es **por store view**, así que operar sobre un subconjunto silencioso
dejaría tiendas sin actualizar sin que nada lo dijera. Si la topología no está
espejada todavía, el argumento se exige en vez de adivinarse.

### Códigos de salida

Una sola definición, `src/skudo/exit_codes.py`, compartida por el CLI y por el
arnés de aceptación —que ya usaba esta convención—:

| Código | Significado |
|---|---|
| `0` | La operación hizo lo que dice. |
| `1` | Fallo de la operación: error del módulo, **deriva detectada**, criterio de aceptación reprobado, estado insuficiente. |
| `2` | **Tenant desconocido.** |
| `3` | Configuración incompleta: falta `SKUDO_DATABASE_URL`, o la variable de entorno donde la fila del tenant declara su token. |
| `64` | Error de **uso**: argumento inválido o comando desconocido. |

El 64 no es decorativo. `argparse` sale con **2** ante un argumento mal
escrito, y 2 ya significa "tenant desconocido": sin separarlos, un `--stores`
mal tecleado y un tenant inexistente serían el mismo código para quien
automatiza. 64 es `EX_USAGE` de `sysexits.h`, y una prueba afirma que los dos
valores son distintos.

`reconcile` sale con 1 cuando hay deriva: un comando de verificación que sale 0
con el espejo desviado no sirve en un cron.

### El token

Ningún comando acepta un token como argumento, y una prueba afirma la
**ausencia de la opción** (`test_no_command_accepts_a_token_on_the_command_line`):
mientras `--token` exista, pasarlo es una invocación válida, y queda en el
historial del shell y en la tabla de procesos, donde cualquier usuario de la
máquina lo lee con un `ps`.

Ningún comando lo imprime. `status` y `register-tenant` informan
`token_env_var_is_set` —si la variable está PUESTA— y nunca su contenido; una
prueba comprueba que el valor no aparece en `stdout` ni en `stderr`, tampoco en
el camino de error.

El token se lee de la variable que la **fila** del tenant nombra
(`config.tenant_token`). El `Settings.tenant_token(code)` anterior lo derivaba
del CÓDIGO del tenant, que era una segunda definición de la misma cosa: en
cuanto un tenant guardara otro nombre en su fila —dos entornos del mismo
cliente, una credencial rotada— el ingestor habría buscado en la variable
equivocada y reportado "falta el token" teniéndolo delante. Hay una prueba que
pone las DOS variables y exige que gane la de la fila.

`TenantSource` sigue construyéndose sólo por `from_tenant()`: el CLI no
introduce ninguna vía nueva.

### El asiento de prueba, declarado

`cli.main(argv, *, transport=None)` acepta un transporte httpx que en
producción es siempre `None`. Existe para que las pruebas ejerciten **este**
comando —su parseo, sus códigos de salida, su manejo de errores, sus
escrituras— y no una reimplementación suya. Toda la verificación sobre HTTP
real de este informe pasa por el mismo `main` con `transport=None`.

`skudo.acceptance.s0.main()` quedó como envoltorio de `skudo.cli accept`:
mantener dos maneras de resolver el tenant y montar la sesión es cómo el
criterio de aceptación termina corriendo contra un tenant resuelto de otra
forma que la ingesta que lo pobló.

---

## 2. La pasada completa: commit por página y reanudación

### El defecto

`full_sync` hacía **un** `session.commit()`, al final. Para el catálogo piloto
son 457.762 upserts más un par delete+insert de categorías por producto en una
sola transacción de Postgres, sin lotes, sin commit por página y sin
reanudación. Un fallo a la tercera hora no dejaba nada, y la pasada siguiente
empezaba de cero. `sync_generation` ya daba un sello reanudable y nadie lo
usaba así.

### Cómo funciona la reanudación

Tabla nueva, `full_sync_checkpoint` (migración **0013**), una fila por
`(tenant, store view)`:

| Columna | Para qué |
|---|---|
| `generation` | El sello de `product_sync_generation_seq` con el que esta pasada marca lo que toca. |
| `next_cursor` | El cursor opaco de `/products` con el que pedir la página siguiente. |
| `pass_complete` | La pasada de ESTA store view vio la última página. **Es la precondición del barrido.** |
| `swept` | El barrido de esta store view ya corrió. |
| `pages_done`, `records_written` | Para que `status` diga por dónde va. |

El checkpoint se escribe **en la misma transacción** que la página que acaba de
aplicarse, así que los datos del espejo y el punto de reanudación avanzan
juntos o no avanzan. No hay estado en el que el cursor vaya por delante de los
datos.

Al arrancar, la pasada busca una **generación abierta** para el tenant: alguna
store view cuyo checkpoint no esté `pass_complete AND swept`. Si la hay,
continúa con **esa misma generación** (`resumed: true` en el reporte) y desde
el cursor guardado; si no, toma una nueva de la secuencia. Una store view que
ya terminó y fue barrida en esta generación no se vuelve a recorrer
(`store_views_already_done`). `--restart` fuerza una generación nueva y
reinicia los checkpoints.

**Por qué la generación tiene que ser la misma.** El barrido borra las filas de
esa store view que no lleven el sello de la pasada. Si la reanudación tomara
una generación nueva, todo lo que la pasada interrumpida ya había escrito
—y que la reanudación no vuelve a leer, porque arranca desde el cursor
guardado— quedaría con el sello viejo, y el barrido del final se lo llevaría:
el espejo terminaría con la última página solamente. La prueba que discrimina
eso es
`test_a_resumed_pass_continues_the_same_generation_so_the_sweep_spares_page_one`.

**El límite de reanudar, declarado.** La reanudación es el DEFAULT porque el
fallo que evita —perder tres horas de pasada— es el que motivó la tarea. El
precio es que una pasada abandonada hace semanas se continúa igual, y las filas
que esa pasada escribió al principio conservan su sello: si el origen borró uno
de esos productos entretanto, el barrido del final no lo va a soltar (lleva el
sello de la generación en curso). `delta_sync` sí lo borra si la cola registró
el evento, y `--restart` fuerza una pasada limpia. `status` muestra
`resumable: true` con la fecha del checkpoint para que la decisión sea
informada en vez de implícita.

### El barrido, imposible sobre una pasada a medias

Con commit por página, una pasada interrumpida deja el espejo **parcialmente
actualizado**. Eso es aceptable: lo que había sigue siendo el último hecho
observado. Lo que deja de ser aceptable es el barrido: barrer "lo que esta
pasada no selló" sobre una pasada que vio tres páginas de cuatrocientas
borraría casi todo el catálogo.

La precondición no está en el llamador. Está **dentro de `_sweep`**, y se lee
de la BASE, no de una variable local:

```python
checkpoint = _checkpoint(session, tenant_id, store_id)
if checkpoint is None or checkpoint.generation != generation:
    raise IncompletePassSweep(...)
if not checkpoint.pass_complete:
    raise IncompletePassSweep(...)
```

Así, un barrido sobre una pasada incompleta no es una llamada que haya que
recordar no hacer: no es expresable. Y `pass_complete` se escribe en la misma
transacción que la ÚLTIMA página, no antes: si ese commit no llega, la pasada
sigue estando a medias y el barrido sigue prohibido.

### Cómo se probó que una interrupción no dispara un barrido indebido

Tres capas, y las tres fallan si el mecanismo se relaja:

**1. Sobre el mecanismo (`tests/ingest/test_full_sync_resume.py`).** Se siembra
en el espejo una fila que el origen ya no ofrece, se corta la pasada en la
segunda página con un transporte que lanza una excepción, y se afirma que la
fila **sigue ahí** y que `swept` es `false`. Además se llama a `_sweep` a mano
sobre la pasada a medias y se exige `IncompletePassSweep` con nada borrado, y
se llama con una generación ajena —el caso en que el barrido borraría TODAS las
filas de la store view— y se exige el mismo rechazo.

**2. A escala (`tests/ingest/test_full_sync_scale.py`).** Corte en la página
**11 de 20**: media pasada confirmada (5.501 filas visibles desde otra
conexión), la fila rancia intacta, y la reanudación continúa la misma
generación desde la página 11 (`cursors[0] == "5500"`), termina las 10 páginas
que faltaban, y **entonces** barre — una fila, la rancia.

**3. Sobre HTTP real, matando el proceso.** No una excepción inyectada: un
`SIGKILL` a mitad de la pasada contra la instancia de desarrollo.

```
$ docker exec … psql -c "INSERT INTO product_record (… sku …) VALUES (…, 'RANCIO-H3', …, 0)"
INSERT 0 1

$ timeout --signal=KILL 3 python -m skudo.cli full-sync --tenant skudodev --restart --page-size 1
Terminado (killed)   EXIT=137

 store_view_magento_id | generation | pages_done | pass_complete | swept |   next_cursor
-----------------------+------------+------------+---------------+-------+------------------
                     1 |          3 |          5 | f             | f     | c2t1ZG8xOjEwNQ==
                     3 |          2 |          3 | t             | t     |

 rancio_sigue | total
--------------+-------
            1 |     9        <-- el barrido NO corrió
```

Y la invocación siguiente, sin argumentos nuevos:

```
$ python -m skudo.cli full-sync --tenant skudodev --page-size 1
{ "generation": 3, "resumed": true, "records_written": 11, "records_deleted": 1, … }

 rancio_sigue | total | generaciones
--------------+-------+--------------
            0 |     8 |            1
```

La generación 3 se continuó (no se tomó una nueva), las cinco páginas que la
pasada interrumpida había escrito **no** se barrieron, y lo único que se borró
fue la fila que el origen ya no ofrece. `status` lo dice antes de que nadie
pregunte: `"resumable": true` con su `next_cursor`.

---

## 3. La reparación dirigida de una partición

H1 partió el digest de contenido en 256 particiones por hash del SKU y dejó que
`reconcile()` reporte **qué** particiones divergen. El remedio, sin embargo,
seguía siendo `full_sync`: 228.881 productos y horas de trabajo para reparar
~900. Detección que nombra un lugar con un remedio que lo ignora es media
función.

### El lado del módulo

`/checksums` acepta `partitions=ab,cd` y agrega `partition_skus` al payload:
los SKUs de esas particiones, **de la misma consulta** que produce el digest.
Dos consultas darían el conjunto de un instante y el digest de otro, y la
reparación borraría del espejo un SKU que existe.

Tres decisiones que no son de estilo:

- Se emite una entrada por partición **pedida, incluidas las vacías**. Una
  partición vacía en Magento y poblada en el espejo es justo el caso que hay
  que poder limpiar, y omitirla la volvería indistinguible de "no se
  preguntó".
- Máximo **32** particiones por llamada. Pedir más es pedir una fracción grande
  del catálogo por un endpoint de reconciliación, y a partir de ahí la pasada
  completa es más barata y además cierra la deriva de conjunto.
- Una partición con otra forma es **entrada inválida (400)** vía
  `InputException`, no un 500 con traza — el mismo razonamiento que
  `Model\Cursor` documenta: un 500 es la clase de error que el cliente
  reintenta, y esto no se arregla reintentando. Verificado sobre HTTP real.

`partition_skus` es una **lista de objetos** y no un mapa partición → SKUs, por
la misma razón que `content_partitions`: un array PHP con claves `'00'`/`'10'`
tiene claves mixtas y `json_encode` lo emite como objeto o como array según los
datos. El barrido mecánico de `MapValuedFieldsAreJsonObjectsTest` cubre ahora
también este campo. (La trampa se manifestó en la primera versión de la propia
prueba: un `array_column(...)` por partición convirtió `'21'` en el int `21`.)

### El lado del ingestor

`repair_partitions(session, source, store_view_magento_id, partitions)`, en
`src/skudo/ingest/repair.py`, tres pasos en este orden:

1. Pedir la población **autoritativa** de esas particiones.
2. Borrar del espejo, **en esa store view**, los SKUs de esas particiones que
   la instancia ya no ofrece. Sin este paso la reparación sólo podría cerrar
   divergencias por valor rancio, no por sobra.
3. Releer con `/products-by-sku` los que sí existen y aplicarlos con el
   **mismo** bucle que usa la pasada completa (`apply_items`, factorizado en
   `src/skudo/ingest/apply.py` — era el bucle que H1 nombró como "no
   factorizado" y que por eso hacía imposible escribir esto).

No toca `sync_generation`, igual que `delta_sync`: mover el sello desde una
escritura dirigida haría parecer viva —para el barrido de la próxima pasada
completa— una fila que esa pasada puede no encontrar.

El fallo peligroso está cerrado con una prueba: si la ausencia de
`partition_skus` se leyera como "esta partición está vacía", la reparación
borraría del espejo la cohorte entera. Se **aborta**, igual que
`reconcile._remote_partitions` aborta ante un módulo sin digest de contenido.

### El camino completo, sobre HTTP real

```
$ docker exec skudo_dev_db mysql … -e "UPDATE catalog_product_entity
    SET updated_at='2026-12-25 23:59:59' WHERE sku='SKU-GAMMA';"

$ python -m skudo.cli reconcile --tenant skudodev --stores 1
{"1": {…, "digest_matches": true, "needs_full_sync": false, "content_matches": false,
        "diverging_partitions": [{"partition": "5b", "magento_count": 1, "mirror_count": 1, …}]}}
store 1: deriva de CONTENIDO en 1 de 256 particiones. Remedio dirigido:
  `skudo repair --tenant skudodev --store 1 --partitions 5b`
EXIT=1

$ python -m skudo.cli repair --tenant skudodev --store 1 --partitions 5b
{"store_view_magento_id": 1, "partitions": ["5b"], "skus_reread": 1,
 "records_written": 1, "records_deleted": 0, "skus_not_returned": []}

$ python -m skudo.cli reconcile --tenant skudodev --stores 1   # content_matches: true
```

Un SKU releído, no 228.889. La deriva por **sobra** también se verificó: con un
`FANTASMA-4` sembrado en el espejo dentro de la partición `5b`, el `repair`
explícito lo borró (`records_deleted: 1`) y la reconciliación volvió a 0.

`--from-reconcile` reconcilia primero y repara lo que el reporte nombre, sin
que nadie copie particiones a mano. Y **se niega** cuando la deriva es de
CONJUNTO: falta o sobra un producto y no se sabe qué más, así que la reparación
dirigida no puede cerrarla. Dice eso y sale con 1, en vez de reparar
particiones y dejar creer que el espejo quedó sincronizado.

---

## 4. M7 — la poda de la cola de cambios

### El problema, y por qué no es sólo higiene

`standard_skudo_change_log` es insert-only por diseño (change_id monótono,
seguro de paginar) y no tenía retención, ni comando, ni cron. Una importación
masiva escribe 228k filas y, desde el cierre de H1, una acción del grid sobre
20.000 productos escribe 20.000 donde antes escribía **cero**. La tabla vive en
la base de **producción del cliente**.

Podar es fácil. Podar sin borrar lo que nadie leyó es el problema: el watermark
del consumidor vive del otro lado del cable (`sync_watermark.last_change_id`,
en nuestro Postgres) y la base del cliente no lo conoce. Y una fila borrada
antes de ser leída **no la reemite nadie**: la cola es la única señal de un
`updateWebsites()` y de las escrituras masivas (H1, §5.2), y la reconciliación
sólo detecta el cambio si movió `updated_at`.

### La estrategia elegida: el ingestor lo dice solo, al leer

`/deltas?sinceId=X` significa literalmente "dame los cambios con `change_id`
mayor que X". Así que **la petición es la prueba** de que el consumidor ya
aplicó todo hasta X inclusive — y como `delta_sync` avanza su watermark por
página y sólo después de aplicarla, ese X nunca va por delante de lo aplicado:
es una cota **conservadora**.

`DeltaReader::getChanges()` guarda el máximo histórico de `sinceId` en
`standard_skudo_delta_read` (una fila, `GREATEST`, para que un reintento o un
consumidor recién inicializado no lo hagan **retroceder**), y la poda no toca
nada por encima de ese número.

**Por qué no las alternativas.** Un endpoint de escritura para informar el
watermark habría dado la misma garantía a cambio de una superficie de escritura
en un módulo de sólo lectura y de que la poda dependiera de que alguien se
acuerde de llamarlo. Una retención puramente temporal —"borrá lo de más de N
días"— habría borrado por reloj filas que un ingestor detenido no leyó, y es
exactamente la clase de pérdida silenciosa que este proyecto ya pagó dos veces.

### La regla: dos condiciones, las dos obligatorias

```sql
change_id <= (lo que el ingestor ya consumió)
AND changed_at < NOW() - INTERVAL <margen> DAY
```

La primera es la que hace **imposible** borrar lo no leído. Si el ingestor deja
de leer, el número deja de moverse y la poda deja de borrar: la cola crece, que
es el fallo benigno.

La segunda no es redundante: es el margen que sobrevive a que la primera se
equivoque —un segundo consumidor con otro watermark, una restauración de
nuestro Postgres a un punto anterior—. Es configurable en
`standard_skudo/retention/change_log_days` (default **30**, piso **1**: con 0
la única red sería el watermark) vía `etc/config.xml` y
`etc/adminhtml/system.xml`, así que `bin/magento config:set` la valida y la
acepta.

El corte se calcula **en SQL** (`DATE_SUB(NOW(), ...)`) y no en PHP:
`changed_at` lo escribe MySQL con su reloj y su zona, y una cadena compuesta en
PHP se desplaza en silencio en cuanto los dos relojes o las dos zonas difieren,
borrando de más o de menos sin que nada lo diga. Un solo reloj, sin conversión.

El predicado se escribe **una** vez y lo usan tanto el conteo del ensayo como
el borrado: un `--dry-run` que informara un conjunto distinto del que el
borrado toca daría permiso para borrar otra cosa.

### Lo que se ejecuta

- `bin/magento skudo:changelog:prune [--days=N] [--dry-run]` — la mitad manual,
  para el primer recorte de una cola que ya creció y para poder ensayarlo. Sale
  con `2` (`Command::INVALID`) si el margen está por debajo del piso.
- Cron diario `standard_skudo_change_log_prune` a las 03:27 — la mitad
  desatendida. Con un margen configurado por debajo del piso usa el default en
  vez de fallar cada minuto; el comando, que tiene un humano delante, sí falla.

### Por qué no puede borrar filas no leídas: la comprobación

Sobre la instancia de desarrollo, con el watermark del ingestor en **11** y una
cola de 12 filas donde 9 eran anteriores al margen:

```
$ bin/magento skudo:changelog:prune --dry-run
cola: 12 fila(s); el ingestor consumió hasta change_id 11 (última lectura: 2026-09-10 21:04:41)
se borrarían 8 fila(s) consumidas y con más de 30 día(s)

$ bin/magento skudo:changelog:prune
borradas: 8 fila(s) consumidas y con más de 30 día(s)

 change_id | sku               | changed_at
-----------+-------------------+---------------------
         9 | SKU-ALPHA         | 2026-09-10 20:19:49   <- consumida, pero dentro del margen
        10 | SKU-DELTA         | 2026-09-10 20:19:54
        11 | SKU-DELTA         | 2026-09-10 20:20:31
        12 | SKU-NO-LEIDO-AUN  | 2026-05-01 10:00:00   <- VIEJA, pero NO consumida
```

La fila 12 es el caso entero: cuatro meses de antigüedad, muy por fuera del
margen, y **en pie** porque su `change_id` está por encima del watermark. La
siguiente pasada la leyó normalmente:

```
$ python -m skudo.cli delta-sync --tenant skudodev
{"changes_seen": 1, …, "watermark": 12}
```

Con el watermark en 0 —instancia recién instalada, o ingestor apagado— el
comando **no emite ni un `DELETE`**; hay una prueba que lo afirma sobre las
sentencias, no sobre el conteo.

---

## 5. La escala

### El proxy en la suite

`tests/ingest/test_full_sync_scale.py`: 10.000 productos sintéticos en 20
páginas de 500, con commits de verdad (no el `db_session` de rollback, que no
podría demostrar la propiedad central). Afirma las cuatro propiedades
estructurales:

1. **Completitud.** La pasada termina, escribe los 10.000 registros y las
   20.000 asignaciones de categoría, con un **solo** sello de generación.
2. **Confirma incrementalmente.** Una conexión **independiente** ve
   `0, 500, 1.000, …, 9.500` filas confirmadas en el momento en que la pasada
   pide cada página. Con un único `commit()` al final vería 0 en todas.
3. **Memoria acotada por construcción.** Ningún lote que llega a la base supera
   el tamaño de página, y hay tantos lotes como páginas: no hay ningún punto
   del recorrido en que el proceso tenga el catálogo entero en memoria. Se
   afirma sobre el mecanismo y no sobre una medición de RSS, que sería frágil.
4. **Reanudación a escala.** Corte en la página 11 de 20, sin barrido, y
   continuación de la misma generación desde la página 11.

Comprobado por sabotaje: quitando el commit por página, 2 de las 4 fallan.

### Lo que la medición encontró antes de la pasada grande

Al medir la escritura del espejo —de a una sentencia por producto, como estaba—
salieron **617 productos/s**, y el perfil dijo que el coste dominante no era
Postgres sino **compilar el SQL** en SQLAlchemy, una vez por producto
(`visit_insert`, 6,3 s de 19 s en 2.000 productos; un round-trip trivial a la
base cuesta 0,13 ms y un `upsert_record` costaba 1,10 ms). Para el piloto
—457.762 escrituras— eso son ~12 minutos de puro armado de sentencias.

La página entera se escribe ahora en una sentencia multi-fila: **2.900
productos/s**, 4,7× más rápido, con el RSS plano:

| Productos | Tiempo | Throughput | RSS pico |
|---|---|---|---|
| 5.000 | 2,03 s | 2.464/s | 79 MB |
| 20.000 | 7,00 s | 2.858/s | 81 MB |
| 50.000 | 17,27 s | 2.896/s | 81 MB |

El RSS **no crece** con el catálogo: es la propiedad que hace que 228.881
productos cuesten en RAM lo mismo que 500.

### La pasada grande, a mano, contra la instancia de desarrollo

Se sembraron **228.881 productos sintéticos** en la base de la instancia de
desarrollo (`/home/ingmar/skudo-dev-logs/h3/seed-scale.sql`), con
`created_in = 1` y `updated_in = 2147483647` para que sean la versión aplicada
que el filtro de Magento_Staging admite, más cuatro valores EAV por producto
(`name`, `price`, `status`, `visibility`, repartidos en tres de las cinco
tablas que `eavValues()` recorre), 343.335 asignaciones de website y 457.772 de
categoría. Con los 8 productos que ya había, el módulo sirve **228.889 SKUs**
por store view.

La pasada se corrió por el MISMO camino que el CLI
(`/home/ingmar/skudo-dev-logs/h3/scale-run.py` envuelve
`skudo.cli.main(["full-sync", …])` y sólo instrumenta el reloj), con
`--page-size 1000` (el tope del módulo) y `--restart`:

| Medida | Valor |
|---|---|
| SKUs escritos | **457.778** (228.889 por store view × 2) |
| Asignaciones producto-categoría | 457.771 |
| Páginas pedidas | 458 (229 por store view) |
| **Reloj total** | **2.017 s = 33,6 min** |
| Esperando a Magento | 1.841 s (**91,3 %**), 4,02 s/página |
| Trabajo del ingestor (espejo incluido) | 176 s = 2,9 min |
| **RSS pico del ingestor** | **98 MB** |
| Tamaño del espejo en Postgres | 382 MB |

Y el reporte que devolvió, con las dos store views completas y barridas:

```json
{"generation": 5, "resumed": false, "records_written": 457778,
 "records_deleted": 0, "pages_fetched": 458,
 "store_views_completed": [1, 3], "store_views_already_done": [],
 "records_without_timestamp": 2, "skus_without_timestamp": ["SKU-Z-LAST", "SKU-Z-LAST"]}
```

Lo que estos números dicen, y lo que no:

- **El coste está del lado de Magento, no del ingestor.** El 91,3 % del reloj es
  espera de `/products`. De los 4,02 s por página, ~3,9 s son el **arranque de
  Magento** en esta instancia —medido aparte: una petición a `/environment`,
  que no toca el catálogo, tarda 3,9 s— y sólo ~1,5 s son la consulta de 1.000
  productos con sus cinco tablas EAV, sus websites y sus categorías. La
  instancia de desarrollo corre en modo `developer` detrás de `php -S`, sin
  opcache caliente; en una instalación en modo `production` ese arranque es una
  fracción. Es la razón por la que `--page-size 1000` es el ajuste que más
  ayuda: **halva el número de arranques**.
- **El ingestor cuesta 2,9 min para 457.778 escrituras**, coherente con los
  ~2.900 productos/s medidos en aislamiento.
- **La memoria no depende del catálogo:** 98 MB de RSS pico para 228.889 SKUs
  contra los ~81 MB del banco sintético de 50.000. El ingestor nunca tiene más
  de una página en memoria.

### El criterio de aceptación 1, a la escala que exige

Esto es lo que antes no se podía ni intentar. Sobre el espejo ya poblado:

| Comando | Reloj | RSS pico | Resultado |
|---|---|---|---|
| `reconcile --tenant skudodev` (las dos store views) | **11,3 s** | 168 MB | `magento_count = mirror_count = 228.889`, `digest_matches: true`, **256 particiones comparadas**, `content_matches: true`, salida 0 |
| `delta-sync --tenant skudodev` | 4,3 s | 75 MB | sin cambios pendientes, watermark 12 |
| `accept --tenant skudodev` | **17,4 s** | 168 MB | **los cinco criterios en OK** |

```
[OK ] espejo_sincronizado: sin deriva
[OK ] score_por_store_view: conteos por store view: {1: 228889, 3: 228889}
[OK ] procedencia_de_scope: 500 registros revisados; procedencias: {'global': 2027, 'store': 1, 'website': 1}
[OK ] identidad_de_opciones: 327 opciones con etiqueta distinta por store view reconocidas como una sola opción
[OK ] efecto_de_categoria: 374 producto(s) con efecto de categoría distinto entre store views; 500 par(es) evaluado(s), 0 sin evaluar por website_desconocido, 0 par(es) sin website de la tienda espejado
```

Los 168 MB de `reconcile` son el pico del lado Python (228.889 pares
`(sku, updated_at)` más las 256 particiones). El lado PHP hace un `fetchAll`
del mismo tamaño y ése sigue sin cota — anotado en `pendiente-s0.md`.

### El barrido, medido con 457.762 filas para soltar

Al retirar los productos sintéticos de la instancia
(`/home/ingmar/skudo-dev-logs/h3/unseed-scale.sql`) y volver a correr la pasada
completa, el origen ofrece de nuevo 8 SKUs por store view y el barrido tiene
que soltar todo lo demás:

```
$ skudo full-sync --tenant skudodev --restart
{"generation": 6, "records_written": 16, "records_deleted": 457762, "pages_fetched": 2}
Elapsed (wall clock): 0:13.24        Maximum resident set size: 74 MB
$ skudo reconcile --tenant skudodev   # exit 0
```

13 s para dos páginas y 457.762 borrados. El barrido no es el cuello de
botella, y por eso no se agregó un índice sobre `sync_generation` (§7).

**Y una medición que no se buscaba:** después de ese barrido quedaron
**457.762 filas huérfanas** en `product_category_assignment` —el `_sweep` sólo
borra `ProductRecord`—, que es exactamente el hallazgo **M3** con un número
detrás. Se limpiaron a mano en el espejo de desarrollo; el hallazgo sigue
abierto y ahora se sabe que su coste crece con el catálogo entero, no con la
deriva.

La instancia de desarrollo quedó como estaba (10 filas de entidad, 8 activas) y
su suite de integración —que se salta sola por encima de 5.000 productos
activos— volvió a correr en verde: `4 tests, 27 assertions`.


---

## 6. Las pruebas nuevas, y qué pasa si se borra el comportamiento

| Prueba | Qué falla si el comportamiento se borra |
|---|---|
| `test_an_interrupted_pass_does_not_sweep` | El barrido corre sobre una pasada a medias: la fila que la pasada no llegó a ver desaparece. |
| `test_the_sweep_refuses_a_pass_that_did_not_finish` | `_sweep` deja de exigir el sello persistido y pasa a confiar en el llamador. |
| `test_the_sweep_refuses_a_generation_it_never_registered` | Un barrido con generación ajena borraría TODAS las filas de la store view. |
| `test_a_resumed_pass_continues_the_same_generation_so_the_sweep_spares_page_one` | La reanudación toma una generación nueva y el barrido borra lo que la pasada interrumpida escribió. |
| `test_a_resumed_pass_still_sweeps_what_the_origin_dropped` | Reanudar se vuelve una excusa para no barrer nunca. |
| `test_the_mirror_grows_while_the_pass_runs_and_not_only_at_the_end` | Vuelve el único commit al final (comprobado por sabotaje). |
| `test_no_batch_that_reaches_the_database_is_bigger_than_a_page` | Alguien acumula el catálogo en memoria para escribirlo al final. |
| `test_only_the_skus_of_the_named_partition_are_reread` | La "reparación dirigida" pasa a recorrer el catálogo entero. |
| `test_a_module_without_partition_skus_aborts_instead_of_emptying_the_mirror` | La ausencia del campo se lee como "partición vacía" y la reparación borra la cohorte. |
| `test_no_command_accepts_a_token_on_the_command_line` | Reaparece un `--token` y el secreto viaja por el historial del shell. |
| `test_a_usage_error_does_not_collide_with_the_unknown_tenant_code` | El 2 de `argparse` se confunde con "tenant desconocido". |
| `test_the_conventional_name_is_not_consulted_when_the_row_says_another` | El token vuelve a buscarse por convención en vez de por la fila. |
| `ChangeLogRetentionTest::testWithoutEvidenceOfConsumptionNothingIsDeletedAtAll` | La poda emite un `DELETE` sin constancia de consumo. |
| `ChangeLogRetentionTest::testTheDeleteRequiresBothConsumptionAndTheSafetyMargin` | Cae una de las dos condiciones del borrado. |
| `ChangeLogRetentionTest::testTheCutoffIsComputedBySqlAndNotByPhp` | El corte se compone en PHP y se desplaza por zona horaria. |
| `DeltaReaderTest::testEveryReadRecordsHowFarTheConsumerAsked` | La poda se queda sin su watermark (comprobado por sabotaje: se borró la llamada y la prueba falló). |
| `test_the_same_sku_twice_in_one_batch_keeps_the_last_one` | Un SKU repetido en un lote se vuelve una excepción de Postgres a mitad de una pasada. |
| `test_a_batch_with_mixed_columns_is_refused` | Una fila sin `sync_generation` hereda el sello de sus vecinas. |

---

## 7. Lo que NO se hizo, y por qué

1. **M3 (filas huérfanas en tres tablas).** La reparación dirigida borra el
   `ProductRecord` de un SKU que la partición ya no contiene, pero no sus filas
   de `ProductCategoryAssignment` — exactamente el mismo hueco que `delta_sync`
   tiene documentado. Se dejó consistente con lo que ya hay en vez de arreglar
   medio M3 en un camino y no en los otros dos: es un barrido por tabla, con su
   propio criterio de "qué es una fila huérfana", y merece su tarea. La pasada
   grande le puso número: tras barrer 457.762 `ProductRecord`, quedaron
   **457.762 filas huérfanas** en `product_category_assignment` (§5).

2. **M5 (la mitad de `/products` que ningún test PHP ejecuta).** Sigue abierto.
   La pasada grande de §5 lo ROZA —`eavValues()`, `websiteIds()` y
   `categoryIds()` sirvieron 228.889 productos sobre HTTP real y el espejo los
   recibió— pero eso es una verificación por muestreo de datos sintéticos, no
   la prueba de la semántica del join que M5 pide.

3. **Índice sobre `product_record.sync_generation`.** El barrido filtra por
   `(tenant, store view, sync_generation != g)`; hoy usa el índice de
   `store_view_magento_id`. Medido en el peor caso imaginable —457.762 filas
   para soltar— la pasada entera, barrido incluido, tardó 13 s (§5), así que un
   índice que cuesta en cada uno de los 457.778 upserts habría sido optimizar
   contra la medición y no con ella.

4. **Paralelizar la pasada por store view.** Las dos store views se recorren en
   serie y el 91 % del reloj es espera de Magento, así que dos hilos casi
   dividirían el tiempo por dos. No se hizo porque el checkpoint es por
   `(tenant, store view)` y la generación abierta es por tenant: dos pasadas
   concurrentes sobre el mismo tenant necesitan un candado que hoy no existe, y
   sin él el modo de fallo es el que esta tarea acaba de cerrar (un barrido con
   la generación equivocada). Es una tarea con su propio riesgo.

5. **Retención de la cola por tenant/consumidor múltiple.** El watermark de
   lectura es uno por instancia de Magento. Si dos consumidores distintos
   leyeran la misma instancia con watermarks distintos, gana el mayor y el más
   atrasado podría perder filas. En este diseño un tenant es un Magento y un
   ingestor; el margen de días es la segunda red. Queda **declarado**, no
   resuelto, en el docblock de `Model\DeltaReadWatermark`.

6. **La pasada grande contra el catálogo de PRODUCCIÓN.** No se intentó y no se
   va a intentar: el límite de este proyecto es leer producción, y la medición
   de §5 se hizo sobre datos sintéticos en la instancia de desarrollo. Lo que
   la medición NO cubre es la forma real de los datos del cliente (1.066
   atributos, valores por store view, SKUs con caracteres raros); el orden de
   magnitud sí.

7. **Un test de escala del digest de `/checksums`.** Lo pedía el informe de H1.
   La pasada grande lo ejerció de rebote y con número: `reconcile` sobre
   228.889 SKUs × 2 store views tarda **11,3 s** y llega a 168 MB de RSS del
   lado Python (§5). Lo que sigue sin cota es el `fetchAll` del lado PHP, que
   trae el catálogo entero a memoria en cada llamada; no hay prueba
   automatizada que lo afirme y queda anotado en `pendiente-s0.md` como el
   primer lugar donde este módulo se rompería con un catálogo tres veces
   mayor.

---

## 8. Confirmación de que producción no se tocó

```
$ find /var/www/casanissei.com -newermt '-10 hours' \( -type f -o -type d \)
(sin resultados)

$ ls -la /var/www/casanissei.com/v248/app/etc/env.php
-rwxrwxr-x 1 www-data www-data 5455 jul 21 14:23 .../app/etc/env.php
$ md5sum → c25797f9552cf4a6996f6131ef15473d
```

Mismo mtime y mismo md5 que registran los tres informes anteriores. Ni un
archivo ni un directorio bajo `/var/www/casanissei.com/` fue creado o
modificado; cero `bin/magento` en `v248`; cero
`INSERT`/`UPDATE`/`DELETE`/`ALTER`/`CREATE`/`DROP` contra `nisseicom`; cero
`exec` de escritura en contenedores `*_local`. Del árbol de producción sólo se
usó el `vendor/bin/phpunit` de `v248` para correr la suite del módulo, que es
una lectura.

Toda escritura de esta tarea fue a `/home/ingmar/magento-skudo-dev` (vía
`bin/magento setup:upgrade`, `cache:flush`, `config:set` y el comando de poda),
a la base `skudo_magento` del contenedor `skudo_dev_db`, a las bases
`skudo_httpreal` y `skudo_test` de Postgres, y a
`/home/ingmar/skudo-dev-logs/h3/`.

---

## 9. Artefactos nuevos

| Archivo | Qué |
|---|---|
| `src/skudo/cli.py` | El punto de entrada: once comandos. |
| `src/skudo/exit_codes.py` | La convención de códigos de salida, una sola vez. |
| `src/skudo/ingest/apply.py` | El bucle que traduce items de `/products` a filas del espejo, compartido por los tres caminos. |
| `src/skudo/ingest/repair.py` | La reparación dirigida de particiones. |
| `alembic/versions/0013_full_sync_checkpoint.py` | El punto de reanudación de la pasada completa. |
| `magento-module/.../Model/DeltaReadWatermark.php` | Hasta dónde leyó el ingestor, del lado del cliente. |
| `magento-module/.../Model/ChangeLogRetention.php` | La regla de la poda. |
| `magento-module/.../Console/Command/PruneChangeLog.php` | `bin/magento skudo:changelog:prune`. |
| `magento-module/.../Cron/PruneChangeLog.php` + `etc/crontab.xml` | La poda desatendida. |
| `magento-module/.../etc/config.xml`, `etc/adminhtml/system.xml` | El margen de retención, configurable. |
| `tests/ingest/test_full_sync_resume.py`, `test_full_sync_scale.py`, `test_repair.py`, `tests/test_cli.py` | Las pruebas nuevas del lado Python. |
| `/home/ingmar/skudo-dev-logs/h3/seed-scale.sql` | La siembra de 228.881 productos sintéticos en la instancia de desarrollo. |
| `/home/ingmar/skudo-dev-logs/h3/scale-run.py`, `scale-run.log` | La pasada grande, con el reloj partido entre HTTP y espejo. |
| `/home/ingmar/skudo-dev-logs/h3/scale-verify.sh`, `scale-verify.log` | Lo medido sobre el espejo poblado: `status`, `reconcile`, `delta-sync` y los cinco criterios. |
| `/home/ingmar/skudo-dev-logs/h3/unseed-scale.sql` | Retira los productos sintéticos y deja la instancia como estaba. |
