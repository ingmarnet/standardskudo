# Verificación del módulo `Standard_Skudo` sobre HTTP real

Fecha: 2026-09-10. Entorno construido desde cero para que los ocho endpoints del
módulo y el ingestor Python se ejerciten por primera vez contra un Magento que
corre de verdad, con peticiones HTTP reales y un token de integración real.

Hasta hoy: 105 tests PHPUnit del módulo y 185 del ingestor, **ninguno** había
hablado con un Magento en ejecución. El arreglo del `WebApiEnvelope` estaba
verificado instanciando `ServiceOutputProcessor` a mano; nadie había hecho una
petición.

**Resultado en una línea:** los ocho endpoints responden 200 y el envoltorio
sobrevive, pero se encontraron **9 discrepancias**, una de ellas (C1) que hacía
estallar `full_sync` en el primer producto sin override, y otra (C2) que hace
que el módulo pierda productos en silencio en cualquier instancia con
Magento_Staging — incluida la de producción, donde son 36 productos.

---

## 1. Qué se creó y dónde

### 1.1 Árbol de código

| | |
|---|---|
| Ruta | `/home/ingmar/magento-skudo-dev` |
| Tamaño | 1,3 GB |
| Origen | copia `rsync -a` de `/var/www/casanissei.com/v248` |
| Incluye | `app/`, `vendor/`, `bin/`, `lib/`, `setup/`, `dev/`, `pub/` (sin `static`/`media`/`sitemap`), `composer.json`, `composer.lock`, `.htaccess` |
| Excluye | `pub/static`, `pub/media`, `pub/sitemap`, `var/`, `generated/`, `app/etc/env.php` |
| Versión | Adobe Commerce Enterprise 2.4.8-p3, 622 módulos declarados, 587 habilitados |

`index.php` no existía en la raíz de producción (esta instalación sirve desde
`pub/`), así que `rsync` avisó y siguió; no falta nada.

El módulo bajo prueba está **enlazado**, no copiado, para que lo que se
ejercita sea literalmente el código del repositorio:

```
/home/ingmar/magento-skudo-dev/app/code/Standard/Skudo
  -> /home/ingmar/.gemini/antigravity/scratch/Ecommerce_Arp/magento-module/Standard/Skudo
```

### 1.2 Contenedores nuevos

| Contenedor | Imagen | Volumen | Puerto |
|---|---|---|---|
| `skudo_dev_db` | `docker-nisseicom-production-percona8:latest` | `skudo_dev_db_data` | `127.0.0.1:3316` |
| `skudo_dev_opensearch` | `docker-nisseicom-production-os3-master:latest` | `skudo_dev_os_data` | `127.0.0.1:9299` |

Ninguno comparte nombre, volumen ni puerto con los `*_local` de producción.

`skudo_dev_db` se arrancó con los mismos parámetros de motor que
`percona8_local` (`lower_case_table_names=1`, `utf8mb4` / `utf8mb4_unicode_ci`)
y, tras comprobarlo, con el **mismo `sql_mode` que producción**
(`IGNORE_SPACE,STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION`) — el default de la
imagen incluye `NO_ZERO_DATE` y `ONLY_FULL_GROUP_BY`, que producción no tiene.
La colación de `catalog_product_entity.sku` es `utf8mb4_general_ci` en ambos
lados, que es la premisa del Ruling 1 de `ChecksumReader`.

El contenedor de OpenSearch existe sólo porque `setup:upgrade` valida la
conexión al motor de búsqueda antes de actualizar módulos y aborta si no hay
nodo vivo. Se levantó uno propio en vez de apuntar al `opensearch_local` de
producción.

### 1.3 Base de datos

`skudo_magento` en `skudo_dev_db`, 727 tablas.

- **Esquema**: `mysqldump --no-data --skip-triggers --single-transaction
  --skip-lock-tables` de `nisseicom` (726 tablas, 722 `CREATE TABLE` + 4 vistas)
  y restauración en el contenedor propio.
- **Datos copiados** (sólo estructurales y de configuración, con `mysqldump
  --no-create-info`, siempre lectura):
  `store`, `store_group`, `store_website`, `core_config_data` (3.152),
  `eav_entity_type`, `eav_attribute` (1.240), `catalog_eav_attribute` (1.124),
  `eav_attribute_set` (422), `eav_attribute_group` (6.631),
  `eav_entity_attribute` (45.194), `eav_attribute_option` (50.545),
  `eav_attribute_option_value` (58.589), `setup_module` (93), `patch_list` (445),
  `theme`, `design_config_grid_flat`, las 18 tablas `sequence_*`, y —añadidas
  después, por el motivo que explica §5.3— `customer_eav_attribute`,
  `customer_eav_attribute_website`, `eav_attribute_label`, `eav_form_*`,
  `customer_form_attribute`, `customer_group`, `directory_country_region`,
  `eav_entity_store`.
- **NO se copió** ni un producto ni una categoría del catálogo real (228.881
  productos). El dataset es el sembrado en §3.

Topología heredada de producción: store views **1** (`py`, website 1 `base`) y
**3** (`br`, website 2 `website_br`), ambas con root category 2.
`catalog/price/scope = 1` (Website), locale `es_AR` / `pt_BR`, moneda
`PYG` / `USD`.

### 1.4 `app/etc/env.php`

Escrito de cero (no es una copia): base de datos propia en `127.0.0.1:3316`,
**caché de archivos** (sin Redis), **sesiones de archivos** (sin Redis), sin
Varnish ni `http_cache_hosts`, sin AMQP, `MAGE_MODE` en `developer`, todos los
`cache_types` en 0, y una **clave de `crypt` nueva y aleatoria** — deliberadamente
no la de producción: ningún endpoint de este módulo lee configuración cifrada,
así que no hacía falta copiar ese secreto.

### 1.5 Instalación del módulo

```
bin/magento module:enable Standard_Skudo      -> OK
bin/magento setup:upgrade --keep-generated    -> OK (al cuarto intento, ver §5)
bin/magento setup:di:compile                  -> OK (41 s, 9/9 pasos)
```

`setup_module` en la base **de desarrollo**: `Standard_Skudo | 1.0.0 | 1.0.0`.
La tabla de la cola de cambios se creó ahí y sólo ahí:

```sql
CREATE TABLE `standard_skudo_change_log` (
  `change_id` int unsigned NOT NULL AUTO_INCREMENT,
  `sku` varchar(255) NOT NULL,
  `event` varchar(16) NOT NULL COMMENT 'save|delete',
  `changed_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`change_id`),
  KEY `STANDARD_SKUDO_CHANGE_LOG_SKU` (`sku`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
```

### 1.6 Token de integración

Creado por la API de Magento (`IntegrationServiceInterface` +
`AuthorizationServiceInterface::grantPermissions` + `OauthServiceInterface`), no
por `INSERT`s a mano, para que sea un token emitido por el mismo camino que
usaría un tenant real. Script: `/home/ingmar/skudo-dev-logs/create-integration.php`.

- integración `skudo_dev`, `integration_id = 75`, `consumer_id = 75`
- rol de integración `role_id 2252` (`user_type = 1`)
- 466 filas en `authorization_rule`, de las cuales `allow` para
  `Magento_Backend::admin` y **`Standard_Skudo::read`**
- token: `fsjeu1vivu3wbusdwyj28zn12dnbw5ue`

### 1.7 Servidor HTTP

`php -S` con un router propio que sustituye los rewrites de nginx:

```
PHP_CLI_SERVER_WORKERS=4 php -d memory_limit=2G -d max_execution_time=300 \
  -S 127.0.0.1:8088 -t /home/ingmar/magento-skudo-dev/pub \
  /home/ingmar/magento-skudo-dev/pub/skudo-router.php
```

`nginx_local` no se tocó. Latencia observada: ~5,4 s por petición en modo
developer con todas las cachés apagadas.

### 1.8 Espejo Postgres

Base `skudo_httpreal` en el contenedor `ecommerce_arp-postgres-1` ya existente
(`localhost:55432`), creada nueva para no contaminar `skudo_test` que usa la
suite. `alembic upgrade head` hasta la migración `0012`. Tenant registrado:

```
id=1  code=skudodev  base_url=http://127.0.0.1:8088
token_env_var=SKUDO_TENANT_SKUDODEV_TOKEN
```

### 1.9 Artefactos auxiliares

Todo en `/home/ingmar/skudo-dev-logs/`:

| Archivo | Qué es |
|---|---|
| `nisseicom-schema.sql` | volcado de esquema (lectura de producción) |
| `nisseicom-data.sql`, `extra-data.sql`, `extra-data2.sql` | volcados de datos estructurales |
| `seed.sql` | el dataset sembrado (§3) |
| `create-integration.php` | creación de la integración y el token |
| `run_ingest.py` | driver del lado Python paso por paso |
| `acl-probe.php`, `table-probe.php`, `staging-sql-probe.php`, `private-methods-probe.php` | sondas de diagnóstico |
| `final/*.json` | las respuestas crudas de §2 |
| `responses/*.txt` | las respuestas crudas de la primera pasada, **antes** del arreglo C1 |
| `ProductReader.php.orig`, `acl.xml.orig` | copias previas a los dos cambios de §6 |

---

## 2. Las ocho respuestas crudas

Todas con `Authorization: Bearer fsjeu1vivu3wbusdwyj28zn12dnbw5ue` contra
`http://127.0.0.1:8088/rest/V1/skudo`, después de `setup:di:compile`.
**Los ocho responden HTTP 200.**

Guardadas literalmente en `/home/ingmar/skudo-dev-logs/final/`.

### 2.1 `GET /environment` → 200

```json
[{"edition":"Enterprise","version":"2.4.8-p3","product_entity_key":"row_id","staging_enabled":true,"msi_enabled":true,"default_stock_id":null,"websites":[{"id":2,"code":"website_br","name":"Website Brazil"},{"id":1,"code":"base","name":"Website Paraguay"}],"store_groups":[{"id":1,"website_id":1,"code":"main_website_store","name":"Casa Nissei","root_category_id":2},{"id":2,"website_id":2,"code":"store_br","name":"Casa Nissei","root_category_id":2}],"store_views":[{"id":1,"group_id":1,"code":"py","name":"Paraguay","is_active":true,"locale":"es_AR","currency":"PYG"},{"id":3,"group_id":2,"code":"br","name":"Brasil","is_active":true,"locale":"pt_BR","currency":"USD"}],"counts":{"products":8,"attribute_sets":422,"attributes":1240,"categories":6},"module_version":"1.0.0"}]
```

### 2.2 `GET /products?storeId=1&limit=500` → 200

```json
[{"items":[{"sku":"SKU-ALPHA","mpn":"MPN-ALPHA-001","model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Alpha global","mpn":"MPN-ALPHA-001","url_key":"alpha","status":"1","visibility":"4","marca":"900001","price":"100.000000","cost":"60.000000","weight":"1.500000","description":"Descripcion global de Alpha"},"store_values":{"name":"Alpha Paraguay","price":"110.000000"},"website_ids":[1,2],"category_ids":[10,11],"updated_at":"2026-09-01 08:30:00"},{"sku":"SKU-BETA","mpn":"MPN-BETA-002","model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Beta sin overrides","mpn":"MPN-BETA-002","url_key":"beta","status":"1","visibility":"4","marca":"900001","price":"50.000000","description":"Descripcion global de Beta"},"store_values":{},"website_ids":[1,2],"category_ids":[10],"updated_at":"2026-09-02 08:30:00"},{"sku":"SKU-DELTA","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Delta solo website 1","status":"1","visibility":"4"},"store_values":{},"website_ids":[1],"category_ids":[10,11],"updated_at":"2026-09-04 08:30:00"},{"sku":"sku-alpha-lower","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Alpha minusculas","status":"1","visibility":"4"},"store_values":{},"website_ids":[1,2],"category_ids":[11],"updated_at":"2026-09-05 08:30:00"},{"sku":"SKU-ÑOÑO","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Nono con enie","status":"1","visibility":"4","price":"75.500000"},"store_values":{},"website_ids":[2],"category_ids":[11],"updated_at":"2026-09-06 08:30:00"},{"sku":"SKU-NORMAL","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Normal","status":"1","visibility":"4"},"store_values":{},"website_ids":[1,2],"category_ids":[12],"updated_at":"2026-09-07 08:30:00"},{"sku":"SKU-Z-LAST","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Z Last","status":"1","visibility":"4"},"store_values":{},"website_ids":[],"category_ids":[],"updated_at":"0000-00-00 00:00:00"}],"next_cursor":null}]
```

### 2.3 `GET /products?storeId=3&limit=500` → 200

```json
[{"items":[{"sku":"SKU-ALPHA","mpn":"MPN-ALPHA-001","model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Alpha global","mpn":"MPN-ALPHA-001","url_key":"alpha","status":"1","visibility":"4","marca":"900001","price":"100.000000","cost":"60.000000","weight":"1.500000","description":"Descripcion global de Alpha"},"store_values":{"name":"Alpha Brasil","price":"130.000000","cost":"80.000000","description":""},"website_ids":[1,2],"category_ids":[10,11],"updated_at":"2026-09-01 08:30:00"},{"sku":"SKU-BETA","mpn":"MPN-BETA-002","model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Beta sin overrides","mpn":"MPN-BETA-002","url_key":"beta","status":"1","visibility":"4","marca":"900001","price":"50.000000","description":"Descripcion global de Beta"},"store_values":{},"website_ids":[1,2],"category_ids":[10],"updated_at":"2026-09-02 08:30:00"},{"sku":"SKU-DELTA","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Delta solo website 1","status":"1","visibility":"4"},"store_values":{},"website_ids":[1],"category_ids":[10,11],"updated_at":"2026-09-04 08:30:00"},{"sku":"sku-alpha-lower","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Alpha minusculas","status":"1","visibility":"4"},"store_values":{},"website_ids":[1,2],"category_ids":[11],"updated_at":"2026-09-05 08:30:00"},{"sku":"SKU-ÑOÑO","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Nono con enie","status":"1","visibility":"4","price":"75.500000"},"store_values":{"name":"Nono em portugues"},"website_ids":[2],"category_ids":[11],"updated_at":"2026-09-06 08:30:00"},{"sku":"SKU-NORMAL","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Normal","status":"1","visibility":"4"},"store_values":{},"website_ids":[1,2],"category_ids":[12],"updated_at":"2026-09-07 08:30:00"},{"sku":"SKU-Z-LAST","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Z Last","status":"1","visibility":"4"},"store_values":{},"website_ids":[],"category_ids":[],"updated_at":"0000-00-00 00:00:00"}],"next_cursor":null}]
```

### 2.4 `POST /products-by-sku` → 200

Cuerpo enviado:
`{"storeId":1,"skus":["SKU-ALPHA","SKU-GAMMA","SKU-Z-LAST","SKU-EXPIRED","SKU-ÑOÑO","NO-EXISTE"]}`

```json
[{"items":[{"sku":"SKU-ALPHA","mpn":"MPN-ALPHA-001","model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Alpha global","mpn":"MPN-ALPHA-001","url_key":"alpha","status":"1","visibility":"4","marca":"900001","price":"100.000000","cost":"60.000000","weight":"1.500000","description":"Descripcion global de Alpha"},"store_values":{"name":"Alpha Paraguay","price":"110.000000"},"website_ids":[1,2],"category_ids":[10,11],"updated_at":"2026-09-01 08:30:00"},{"sku":"SKU-ÑOÑO","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Nono con enie","status":"1","visibility":"4","price":"75.500000"},"store_values":{},"website_ids":[2],"category_ids":[11],"updated_at":"2026-09-06 08:30:00"},{"sku":"SKU-Z-LAST","mpn":null,"model":null,"gtin":null,"variant_key":null,"attribute_set_id":4,"type_id":"simple","global_values":{"name":"Z Last","status":"1","visibility":"4"},"store_values":{},"website_ids":[],"category_ids":[],"updated_at":"0000-00-00 00:00:00"}]}]
```

Se pidieron 6 SKUs, volvieron 3. `NO-EXISTE` y `SKU-EXPIRED` faltan
correctamente. `SKU-GAMMA` falta **incorrectamente**: ver C2.
El SKU con acento viajó intacto en un cuerpo JSON (ida y vuelta).

### 2.5 `GET /deltas?sinceId=0&limit=1000` → 200

```json
[{"items":[{"change_id":1,"sku":"SKU-ALPHA","event":"save","changed_at":"2026-09-10 05:48:54"},{"change_id":2,"sku":"SKU-BETA","event":"save","changed_at":"2026-09-10 05:48:54"},{"change_id":3,"sku":"SKU-Z-LAST","event":"delete","changed_at":"2026-09-10 06:48:54"},{"change_id":4,"sku":"SKU-Z-LAST","event":"save","changed_at":"2026-09-10 07:18:54"},{"change_id":5,"sku":"SKU-EXPIRED","event":"delete","changed_at":"2026-09-10 07:38:54"}],"last_change_id":5}]
```

Y con `sinceTimestamp` (`?sinceId=0&limit=1000&sinceTimestamp=<ahora-7200>`),
**byte por byte la misma respuesta**: cero centinelas `change_id: 0`. Ver C3.

```json
[{"items":[{"change_id":1,"sku":"SKU-ALPHA","event":"save","changed_at":"2026-09-10 05:48:54"},{"change_id":2,"sku":"SKU-BETA","event":"save","changed_at":"2026-09-10 05:48:54"},{"change_id":3,"sku":"SKU-Z-LAST","event":"delete","changed_at":"2026-09-10 06:48:54"},{"change_id":4,"sku":"SKU-Z-LAST","event":"save","changed_at":"2026-09-10 07:18:54"},{"change_id":5,"sku":"SKU-EXPIRED","event":"delete","changed_at":"2026-09-10 07:38:54"}],"last_change_id":5}]
```

Y con la cola agotada (`?sinceId=5`): `[{"items":[],"last_change_id":null}]`.

### 2.6 `GET /signals?storeId=1&days=90` → 200

```json
[{"items":[{"sku":"SKU-ALPHA","units_sold":3,"revenue":300,"salable_qty":40,"physical_qty":42,"uses_msi":true,"margin":0.4545,"search_demand":12},{"sku":"SKU-BETA","units_sold":1,"revenue":null,"salable_qty":null,"physical_qty":null,"uses_msi":true,"margin":null,"search_demand":0},{"sku":"SKU-GAMMA","units_sold":2,"revenue":50,"salable_qty":4,"physical_qty":null,"uses_msi":true,"margin":null,"search_demand":0}]}]
```

`GET /signals?storeId=3&days=90` → 200

```json
[{"items":[{"sku":"SKU-ALPHA","units_sold":5,"revenue":500,"salable_qty":7,"physical_qty":42,"uses_msi":true,"margin":0.3846,"search_demand":null},{"sku":"SKU-ÑOÑO","units_sold":1,"revenue":75.5,"salable_qty":null,"physical_qty":null,"uses_msi":true,"margin":null,"search_demand":null}]}]
```

### 2.7 `GET /checksums?storeId=1` → 200

```json
[{"product_count":7,"sku_digest":"7589f256b03ee00d330b9d982404e3fae8bbb0094d24a054d8d85baeefceac1a"}]
```

`GET /checksums?storeId=3` → 200 — **idéntica** (documentado: `storeId` no filtra).

```json
[{"product_count":7,"sku_digest":"7589f256b03ee00d330b9d982404e3fae8bbb0094d24a054d8d85baeefceac1a"}]
```

### 2.8 `GET /attributes?limit=1000` → 200

2.991.414 bytes. `items: 1000`, `next_cursor: "c2t1ZG8xOjEyMzM="`
(base64 de `skudo1:1233`), 50.089 opciones en esa página.
Respuesta completa en `final/attributes-full.json`; el primer item y las
opciones que importan:

```json
{"code":"name","label":"Product Name","frontend_input":"text","declared_scope":"store","is_filterable":false,"is_required":true,"attribute_set_ids":[4,14,16,73,74,77,...],"options":[]}
```

```json
{"option_id":900001,"labels":{"0":"Marca Skudo","1":"Marca Skudo PY","3":"Marca Skudo BR"}}
{"option_id":900002,"labels":{"0":"Solo etiqueta admin"}}
{"option_id":900003,"labels":{"0":"Cero y Uno","1":"Zero and One"}}
{"option_id":54706,"labels":{"0":"Marffim"}}
{"option_id":16551,"labels":{"1":"Gris Techno","0":"Gris Techno","3":"Cinza Techno"}}
{"option_id":9473,"labels":{"1":"Plata/Nácar","0":"Silver/Nacre","3":"Prata/Madrepérola"}}
```

### 2.9 `GET /categories?limit=500` → 200

```json
[{"items":[{"category_id":1,"path":[1],"default_name":"Root Catalog","store_states":[{"store_id":1,"is_active":false,"name":"Root Catalog"},{"store_id":3,"is_active":false,"name":"Root Catalog"}]},{"category_id":2,"path":[1,2],"default_name":"Default Category","store_states":[{"store_id":1,"is_active":true,"name":"Default Category"},{"store_id":3,"is_active":true,"name":"Default Category"}]},{"category_id":10,"path":[1,2,10],"default_name":"Electro","store_states":[{"store_id":1,"is_active":true,"name":"Electro"},{"store_id":3,"is_active":false,"name":"Eletro BR"}]},{"category_id":11,"path":[1,2,11],"default_name":"Audio","store_states":[{"store_id":1,"is_active":true,"name":"Audio"},{"store_id":3,"is_active":true,"name":"Audio"}]},{"category_id":12,"path":[1,99,12],"default_name":"Categoria Huerfana","store_states":[{"store_id":1,"is_active":true,"name":"Categoria Huerfana"},{"store_id":3,"is_active":true,"name":"Categoria Huerfana"}]},{"category_id":99,"path":[1,99],"default_name":"Raiz Fuera de Tienda","store_states":[{"store_id":1,"is_active":true,"name":"Raiz Fuera de Tienda"},{"store_id":3,"is_active":true,"name":"Raiz Fuera de Tienda"}]}],"next_cursor":null}]
```

### 2.10 Caminos de error

| Petición | HTTP | Cuerpo (resumido) |
|---|---|---|
| sin `Authorization` | **401** | — |
| `/products?cursor=BASURA` | **500** | `{"message":"cursor inválido","trace":"..."}` |
| `/products-by-sku` con 101 SKUs | **400** | `{"message":"no se pueden pedir más de %1 SKUs por llamada (se recibieron %2)","parameters":[100,101]}` |
| `/checksums` sin `storeId` | 400 | `{"message":"\"%fieldName\" es obligatorio...","parameters":{"fieldName":"storeId"}}` |
| `/products?storeId=999` | **200** | 3 items con `store_values: {}` |
| `/signals?storeId=999` | 200 | `[{"items":[]}]` |

---

## 3. El dataset sembrado

`/home/ingmar/skudo-dev-logs/seed.sql`, aplicado sólo sobre `skudo_magento`.
Los `row_id` y los `entity_id` son deliberadamente distintos (productos:
`row_id` 101-110 vs `entity_id` 1001-1009; categorías: `row_id` 501-599 vs
`entity_id` 1-99) para que confundir una clave con la otra se note.

### Productos (`catalog_product_entity`, staged)

| row_id | entity_id | sku | created_in / updated_in | para qué está |
|---|---|---|---|---|
| 101 | 1001 | `SKU-ALPHA` | 1 / MAX | override de store en 1 **y** en 3; `cost`/`price` por scope |
| 102 | 1002 | `SKU-BETA` | 1 / MAX | **sin ningún override** — el caso que rompió C1 |
| 103 | 1003 | `SKU-GAMMA` | ahora-60 / ahora+86400 | versión **activa** de una actualización programada ya vigente |
| 110 | 1003 | `SKU-GAMMA` | ahora+86400 / MAX | versión **futura**, con el `row_id` más alto de la tabla |
| 104 | 1004 | `SKU-DELTA` | 1 / MAX | **un solo website** (1) |
| 105 | 1005 | `sku-alpha-lower` | 1 / MAX | minúsculas: rompe el orden por colación |
| 106 | 1006 | `SKU-ÑOÑO` | 1 / MAX | acento (UTF-8 `c3 91`); sólo website 2; override de `name` en store 3 |
| 107 | 1007 | `SKU-NORMAL` | 1 / MAX | categoría fuera del árbol de las tiendas |
| 108 | 1008 | `SKU-Z-LAST` | 1 / MAX | **sin websites, sin categorías**, `updated_at = '0000-00-00 00:00:00'` |
| 109 | 1009 | `SKU-EXPIRED` | 1 / ahora-3600 | versión **expirada**: no debe aparecer nunca |

### Categorías (`catalog_category_entity`, también staged)

| row_id | entity_id | path | para qué está |
|---|---|---|---|
| 501 | 1 | `1` | raíz absoluta, **sin fila de `is_active`** (imita producción) |
| 502 | 2 | `1/2` | root category de los dos store groups |
| 599 | 99 | `1/99` | segunda raíz: fuera del árbol de las tiendas |
| 510 | 10 | `1/2/10` | **activa en store 1, inactiva en store 3**; nombre distinto por store |
| 511 | 11 | `1/2/11` | override de `is_active` en store 3 con valor **NULL** → debe heredar el global |
| 512 | 12 | `1/99/12` | cuelga de la raíz 99 |
| 598 | 10 | `1/2/10` | versión **futura** de la 10, `row_id` más alto, `is_active` opuesto |

### Opciones de atributo (`marca`, `attribute_id` 241, `select`, `is_global=1`)

| option_id | store_ids con etiqueta | por qué |
|---|---|---|
| 900001 | 0, 1, 3 | mapa no consecutivo, tres idiomas |
| 900002 | **{0}** | array PHP que **es** una lista: el caso exacto del bug de `labels` |
| 900003 | **{0, 1}** | claves consecutivas desde cero: el otro caso del bug |

Además se conservaron **las 50.545 opciones y 58.589 etiquetas reales** de
producción, de las cuales **45.769 tienen sólo `store_id = 0`** y 32 tienen
exactamente `{0, 1}`: 45.801 opciones con la forma que colapsaría a array JSON.
343 opciones tienen etiquetas distintas entre las store views 1 y 3.

### Señales, inventario y cola

- 2 pedidos (`sales_order` 9001 store 1, 9002 store 3) y 5 líneas, una de ellas
  con `row_total_incl_tax = NULL` (`SKU-BETA`) para forzar `revenue: null`.
- `search_query`: 2 filas, **las dos de la store 1** → la store 3 debe dar
  `search_demand: null`, no 0.
- `cataloginventory_stock_item`: sólo `SKU-ALPHA` (42) y `SKU-GAMMA` (5).
- `inventory_stock_sales_channel`: la misma cadena que producción
  (`base → 2`, `website_br → 3`), así que `default_stock_id` debe ser `null`
  por ambigüedad.
- `inventory_stock_2`: ALPHA 40, GAMMA 4. `inventory_stock_3`: ALPHA 7.
- `standard_skudo_change_log`: 5 filas, con `SKU-Z-LAST` en `delete` (3) y luego
  `save` (4) para probar *last-event-wins*.

---

## 4. Discrepancias encontradas

Nueve. Dos críticas, tres altas, tres medias, una baja.

---

### C1 — CRÍTICA — `global_values` y `store_values` viajaban como array JSON, no como objeto

**Es el mismo bug que el de las etiquetas de opción, en otro campo, y no estaba
arreglado.**

`ProductReader::eavValues()` devuelve `array<string, string>`. Cuando un
producto no tiene override en la store view pedida, ese array está **vacío**, y
un array PHP vacío es indistinguible de una lista: `json_encode([])` emite `[]`.
La respuesta real, antes del arreglo, para el segundo producto de la primera
página:

```json
{"sku":"SKU-BETA", ..., "store_values":[], ...}
```

El cliente hace `resolve_scope(item["global_values"], item["store_values"], scopes)`
y `resolve_scope` hace `store_values.items()`. Sobre una lista de Python eso es:

```
File "src/skudo/ingest/full_sync.py", line 101, in full_sync
    effective, provenance = resolve_scope(
File "src/skudo/mirror/products.py", line 88, in resolve_scope
    for code, value in store_values.items():
AttributeError: 'list' object has no attribute 'items'
```

`full_sync` **muere en el primer producto sin override de store view**, con la
excepción sin capturar y sin commit. En el catálogo real serían prácticamente
todos: sólo 29 de los 1.066 atributos de producto declaran scope de store.
`delta_sync` muere por la misma línea. Es decir: **antes de este ejercicio, el
lado Python no podía ingerir ni un producto de un Magento real.**

Los 105 tests PHPUnit no lo veían porque `ProductReaderTest` **no afirma nada
sobre `global_values` ni `store_values`** (cero coincidencias de esas dos
cadenas en el archivo de test) y `FakeSelect::join()` devuelve `$this` sin
hacer nada, así que `eavValues()` nunca se ejecutó. Los 185 tests Python no lo
veían porque todos pasan diccionarios literales.

Lo irónico: el docblock de `AttributeReader` dedica 25 líneas a explicar
exactamente este colapso y a justificar el `(object)` de `labels` — y la regla
no se aplicó al mapa de valores EAV, que tiene la misma forma y el mismo
riesgo.

**Arreglado** (ver §6.1), porque sin eso no había forma de seguir.

---

### C2 — CRÍTICA — `ActiveVersionResolver` se compone con el filtro propio de Magento_Staging y el módulo pierde productos en silencio

Adobe Commerce registra `Magento\Staging\Model\Select\FromRenderer` como
renderer de la parte `FROM` de todo `Select`
(`vendor/magento/module-staging/etc/di.xml:69`). El resultado es que **cualquier
consulta que haga `FROM` de una tabla staged recibe automáticamente**, al
ensamblarse:

```sql
AND <alias>.created_in <= :versionId AND <alias>.updated_in > :versionId
```

donde `:versionId` es `VersionManager->getVersion()->getId()`, que en una
petición REST normal (sin preview) vale **1**.

El módulo añade encima su propia ventana, por reloj. El SQL que sale de
`ProductReader::getPage()` en la instancia real es:

```sql
SELECT `e`.`sku` FROM `catalog_product_entity` AS `e`
WHERE ((e.created_in <= UNIX_TIMESTAMP()) AND (e.updated_in > UNIX_TIMESTAMP()))
  AND (e.created_in <= 1) AND (e.updated_in > 1)
ORDER BY `e`.`row_id` ASC
```

Los dos filtros **no son el mismo criterio**: Magento compara contra un *id de
versión*, el módulo contra el *reloj*. Su conjunción es estrictamente más
estrecha que cualquiera de los dos. Medido en la instancia sembrada:

```
SKUs que devuelve el módulo:                     [ALPHA, BETA, DELTA, sku-alpha-lower, ÑOÑO, NORMAL, Z-LAST]   -> 7
SKUs activos por reloj, sin el filtro de Magento: [ALPHA, BETA, GAMMA, DELTA, sku-alpha-lower, ÑOÑO, NORMAL, Z-LAST] -> 8
```

`SKU-GAMMA` —cuya versión **activa** empezó hace 60 segundos y cuyo
`created_in` es por tanto un timestamp > 1— **desaparece de `/products`, de
`/products-by-sku`, de `/checksums` y de `/categories` como asignación.**

Medido contra el catálogo de producción (sólo lectura, `SELECT`):

| | filas |
|---|---|
| filas totales de `catalog_product_entity` | 228.881 |
| entidades distintas | 228.517 |
| filas con `created_in > 1` | 385 |
| pasan la regla del **reloj** (la del módulo) | 228.487 |
| pasan la regla del **id de versión 1** (la de Magento) | 228.481 |
| **entidades sin NINGUNA fila que pase la regla de Magento** | **36** |
| entidades sin ninguna fila que pase la regla del módulo | 30 |
| entidades con más de una fila activa por reloj | 0 |

Ejemplos reales (`created_in` en el pasado, `updated_in` = MAX, es decir:
actualizaciones programadas ya vigentes y permanentes):

```
entity_id 1589449  row_id 170950  TL-F04K004-5268-28  created_in 1689220740  updated_in 2147483647
entity_id 1596842  row_id 178343  128690              created_in 1717646340  updated_in 2147483647
```

**Consecuencia:** 36 productos del catálogo de producción son inalcanzables por
los ocho endpoints. Y lo peor es que **la reconciliación no lo detecta**:
`/checksums` sufre exactamente el mismo doble filtro que `/products`, así que
los dos lados coinciden sobre el conjunto equivocado y `reconcile()` reporta
"sin deriva" para siempre. Es el escenario que `ChecksumReader` existe para
prevenir, producido por el mecanismo que se suponía la red de seguridad.

Corolario adicional: la premisa del docblock de `ActiveVersionResolver` —"sin
este filtro, `catalog_product_entity` no tiene una fila por producto sino una
por versión… una versión programada a futuro siempre tiene la clave más alta:
ganaría el upsert"— **no se sostiene en una petición real de Adobe Commerce**:
Magento ya lo impide por su cuenta. En la instancia sembrada, la versión futura
de `SKU-GAMMA` (`row_id` 110, el más alto) y la versión futura de la categoría
10 (`row_id` 598) quedan fuera **por el filtro de Magento**, no por el del
módulo. Lo único que el filtro del módulo aporta de verdad es excluir versiones
**expiradas** (`SKU-EXPIRED`, que la regla de Magento sí admite porque tiene
`created_in = 1`), y a cambio pierde las programadas vigentes.

No se arregló: elegir entre las dos reglas es una decisión de diseño, no un
parche.

---

### A1 — ALTA — `/signals` reporta SKUs que `/products` no reporta, y el espejo queda con una señal huérfana

`SignalReader::salesRows()` hace `FROM sales_order_item`, que **no es una tabla
staged**, así que no recibe el filtro de Magento. Por eso `/signals?storeId=1`
devuelve `SKU-GAMMA`, mientras `/products?storeId=1` no lo devuelve nunca (C2).

Tras la pasada completa, en el espejo:

```
 sku       | store | tiene_product_record
-----------+-------+---------------------
 SKU-GAMMA |     1 |                    0
```

`product_signal` tiene una fila de señal comercial para un SKU del que el
espejo no tiene ni un `product_record` en ninguna store view. Cualquier
priorización "por dinero" que una las dos tablas lo pierde o lo une mal, y el
espejo afirma una señal sobre un producto que no puede describir.

---

### A2 — ALTA — `physical_qty` llega `null` para un producto que **sí** tiene fila de stock

Peor que A1, porque fabrica un *desconocido* a partir de un *conocido* —
exactamente lo que el spec nombra como el mayor riesgo del sistema.

`SignalReader::attachInventory()` lee la cantidad física uniendo
`cataloginventory_stock_item` con `catalog_product_entity`. Ese join recibe el
filtro de Staging **en la condición del JOIN** (así lo inyecta `FromRenderer`):

```sql
INNER JOIN `catalog_product_entity` AS `e`
  ON e.entity_id = si.product_id AND (e.created_in <= 1 AND e.updated_in > 1)
```

Para `SKU-GAMMA` el join no encuentra fila, así que `physical_qty` se queda en
`null` — pese a que `cataloginventory_stock_item` tiene `product_id = 1003,
qty = 5`. En la misma respuesta, `salable_qty: 4` **sí** llega, porque
`inventory_stock_2` no es una tabla staged y no lleva filtro:

```json
{"sku":"SKU-GAMMA","units_sold":2,"revenue":50,"salable_qty":4,"physical_qty":null,"uses_msi":true,"margin":null,"search_demand":0}
```

Una fila donde lo vendible se conoce y lo físico "no se sabe" es una
contradicción interna del payload, y el consumidor no tiene forma de
distinguirla de un producto realmente sin stock cargado.

---

### A3 — ALTA — el camino de `sinceTimestamp` de `/deltas` está muerto sobre HTTP real

`DeltaReader::activatedVersions()` pide `created_in > $sinceTimestamp`. El
filtro de Magento añade `created_in <= 1`. La conjunción es **insatisfacible**
para cualquier `$sinceTimestamp >= 1`. El SQL que se ensambla de verdad:

```sql
SELECT `catalog_product_entity`.`sku`, `catalog_product_entity`.`created_in`
FROM `catalog_product_entity`
WHERE ((created_in > 1789022795) AND (created_in <= UNIX_TIMESTAMP()) AND (updated_in > UNIX_TIMESTAMP()))
  AND (catalog_product_entity.created_in <= 1) AND (catalog_product_entity.updated_in > 1)

filas devueltas: 0
la misma consulta sin el filtro de Staging: 1  ->  [{"sku":"SKU-GAMMA","created_in":"1789025363"}]
```

Comprobado también por HTTP: `/deltas?sinceId=0&limit=1000&sinceTimestamp=<ahora-7200>`
devuelve **byte por byte** la misma respuesta que sin el parámetro, con cero
centinelas `change_id: 0`, aunque `SKU-GAMMA` se activó dentro de la ventana.

La segunda pasada de `delta_sync` (la que sí envía `sinceTimestamp`, porque
`last_delta_read_at` ya está escrito) confirma el efecto: `changes_seen: 1`,
que es sólo la fila de cola nueva.

Es decir: el mecanismo que existe para que una actualización programada llegue
al espejo cuando se activa —lo único que ni el observer ni `reconcile()`
pueden detectar— nunca dispara. El espejo servirá el valor viejo
indefinidamente.

Nota: el resto de `iter_deltas` **sí** funciona. Recorre la cola real sin
abortar, respeta el orden ascendente de `change_id`, resuelve la colisión de
`SKU-Z-LAST` (delete en 3, save en 4) con *last-event-wins* y avanza el
watermark a 5 y luego a 6.

---

### M1 — MEDIA — `/environment.counts.products` y `/checksums.product_count` no coinciden

En la misma instancia y el mismo ciclo, dos endpoints del mismo módulo dan
números distintos de productos: `counts.products = 8` frente a
`product_count = 7`.

`EnvironmentProbe::counts()` hace un `COUNT(*)` sin filtro propio, así que sólo
recibe el de Magento (8 filas, incluida `SKU-EXPIRED`). `/products` y
`/checksums` añaden la ventana del módulo (7, sin la expirada). Nadie usa
`counts.products` para reconciliar, pero el número se **guarda en el espejo**
(`environment_snapshot.payload`) como si fuera el tamaño del catálogo.

De paso: `counts.attributes = 1240` cuenta `eav_attribute` de **todos** los
entity types, mientras `/attributes` sólo pagina los 1.066 de
`catalog_product`. El nombre del campo invita a compararlos.

---

### M2 — MEDIA — un cursor inválido devuelve HTTP 500, no 4xx, con traza completa

`Cursor::decode()` lanza `\InvalidArgumentException`, que no es una excepción de
Magento, así que el framework la renderiza como error de servidor:

```
GET /products?storeId=1&limit=3&cursor=BASURA  ->  500
{"message":"cursor inválido","trace":"#0 .../magento-module/Standard/Skudo/Model/ProductReader.php(34): ..."}
```

Un cursor lo provee el cliente: es una entrada inválida (400), no un fallo del
servidor. El cliente Python hace `raise_for_status()` y verá un 500, que
normalmente se reintenta; un 400 no. Además el cuerpo lleva la traza con rutas
absolutas del sistema de archivos (en modo `developer`; en producción Magento
la omite). Contrasta con el tope de 100 SKUs, que sí usa `InputException` y sí
devuelve 400.

---

### M3 — MEDIA — `storeId` inexistente no se valida: devuelve 200 con los valores globales

```
GET /products?storeId=999&limit=3  ->  200, 3 items, store_values: {}
GET /signals?storeId=999&days=90   ->  200, {"items":[]}
```

Ninguno de los dos comprueba que la store view exista. Un `storeId` mal escrito
en la configuración de un tenant hace que el ingestor espeje los valores
globales **como si fueran la verdad de esa tienda**, sin un solo error. Y como
`/checksums` tampoco filtra por store, `reconcile()` diría "sin deriva".

---

### B1 — BAJA — el mismo campo llega como `int` o como `float` según el valor, y los `%1` de los mensajes de error no se interpolan

Tipos JSON observados en `/signals`:

```
store 1: revenue -> int   (300)     salable_qty -> int   (40)   margin -> float (0.4545)
store 3: revenue -> float (75.5)    salable_qty -> int   (7)    margin -> float (0.3846)
```

`(float) 300.00` se serializa como `300`. Las columnas del espejo son
`Numeric(18,4)`, así que no hay pérdida — pero un consumidor con validación
estricta de tipos vería `int` donde el contrato dice `float|null`.

Y en los errores, los placeholders de `__()` viajan sin interpolar:

```json
{"message":"no se pueden pedir más de %1 SKUs por llamada (se recibieron %2)","parameters":[100,101]}
```

El cliente tendría que reconstruir el mensaje él mismo.

---

## 5. Lo que confirmó que **sí** funciona

### 5.1 El `WebApiEnvelope` sobrevive de punta a punta — **sí**

Los ocho endpoints devuelven `[{...}]`: una lista JSON con **un solo objeto**,
con las claves de primer nivel intactas. Verificado después de
`setup:di:compile`, que es cuando los interceptores están generados:

```
environment      HTTP=200  list[1]  ['edition','version','product_entity_key',...]
products         HTTP=200  list[1]  ['items','next_cursor']
products-by-sku  HTTP=200  list[1]  ['items']
deltas           HTTP=200  list[1]  ['items','last_change_id']
signals          HTTP=200  list[1]  ['items']
checksums        HTTP=200  list[1]  ['product_count','sku_digest']
attributes       HTTP=200  list[1]  ['items','next_cursor']
categories       HTTP=200  list[1]  ['items','next_cursor']
```

`unwrap()` del cliente los aceptó todos sin lanzar. El arreglo era correcto.

### 5.2 Los `labels` llegan como objeto JSON, no como array — **sí**

Éste era el bug que afectaba a 45.801 de las 50.545 opciones del catálogo. Sobre
la respuesta real de `/attributes?limit=1000`:

```
total de opciones en la página: 50089
tipos JSON de `labels`:         {'dict': 50089}
ejemplos no-dict:               []
```

**Las 50.089 son objetos**, incluidas las dos formas que colapsarían:

```json
{"option_id":900002,"labels":{"0":"Solo etiqueta admin"}}          // una sola clave, 0
{"option_id":900003,"labels":{"0":"Cero y Uno","1":"Zero and One"}} // claves 0 y 1 consecutivas
{"option_id":54706,"labels":{"0":"Marffim"}}                        // dato REAL de producción
```

Y el `_labels_by_store_id()` del cliente, que lanza `TypeError` a propósito si
recibe una lista, no lanzó ni una vez en 50.535 opciones ingeridas.

### 5.3 El digest coincide entre `sort($skus, SORT_STRING)` de PHP y `sorted()` de Python — **sí**

El conjunto se sembró justamente para romper cualquier orden dependiente de
colación (mayúsculas, minúsculas y un acento):

```
PHP    sort($skus, SORT_STRING): ["SKU-ALPHA","SKU-BETA","SKU-DELTA","SKU-NORMAL","SKU-Z-LAST","SKU-ÑOÑO","sku-alpha-lower"]
Python sorted():                 ['SKU-ALPHA','SKU-BETA','SKU-DELTA','SKU-NORMAL','SKU-Z-LAST','SKU-ÑOÑO','sku-alpha-lower']

sku_digest de /checksums (PHP): 7589f256b03ee00d330b9d982404e3fae8bbb0094d24a054d8d85baeefceac1a
sku_digest de reconcile (Python): 7589f256b03ee00d330b9d982404e3fae8bbb0094d24a054d8d85baeefceac1a
COINCIDEN: True
```

Y el `ORDER BY` de MySQL que el Ruling 1 prohíbe da, en efecto, otro orden:

```
SKU-ALPHA | sku-alpha-lower | SKU-BETA | SKU-DELTA | SKU-ÑOÑO | SKU-NORMAL | SKU-Z-LAST
```

El Ruling 1 queda validado empíricamente sobre datos que lo habrían roto.

### 5.4 `eavValues()`, `websiteIds()` y `categoryIds()` — ejecutados por primera vez

Ningún test PHP los ejecuta (`FakeSelect::join()` es un no-op). Invocados por
reflexión sobre la instancia real:

```
eavValues(row_id, [101,102,104,108], store 0) -> los cuatro con sus códigos y valores
eavValues(row_id, [101,102,104,108], store 3) -> 101 => {name:'Alpha Brasil', price:'130.000000', cost:'80.000000', description:''}
websiteIds([101,102,104,108], row_id)         -> 101=>[1,2]  102=>[1,2]  104=>[1]   (108 ausente -> [])
categoryIds([ALPHA,BETA,DELTA,Z-LAST,GAMMA])  -> ALPHA=>[10,11] BETA=>[10] DELTA=>[10,11]
```

- **`eavValues()`**: correcta en contenido, **incorrecta en forma** cuando está
  vacía. Ése es C1. La regla de presencia-no-verdad funciona: el override de
  `description` en la store 3 es `""` y aparece en `store_values` (no se
  colapsa con la herencia global).
- **`websiteIds()`**: correcta. La unión por `e.entity_id = w.product_id`
  —no por `row_id`— resuelve bien con `entity_id` 1001-1009 y `row_id` 101-110,
  que es la comprobación que valida el comentario del código. `SKU-Z-LAST` sin
  filas devuelve `[]`, y `SKU-DELTA` devuelve `[1]`, un solo website.
- **`categoryIds()`**: correcta y **sin duplicados**. Aviso: la duplicación que
  el docblock describe (`NGO-T2092` con 3 filas de versión devolviendo la
  categoría 603 tres veces) **no es reproducible en una petición real**, porque
  el filtro de Magento ya deja como máximo una fila de versión por entidad. El
  `applyActiveVersionFilter()` de este método es, en la práctica, redundante.

### 5.5 `iter_deltas` recorre una cola real sin abortar — **sí**

5 filas, orden ascendente respetado, `last_change_id: 5`, ninguna de las tres
guardas de avance disparó. `SKU-Z-LAST` con `delete` (3) y `save` (4) en la
misma página se resolvió como refresco, no como borrado. Con la cola agotada
devuelve `{"items":[],"last_change_id":null}` y el bucle termina. La parte que
falla es sólo `sinceTimestamp` (A3).

### 5.6 Otras cosas que se comprobaron y salieron bien

- **Paginación por cursor** en `/products` (`limit=3`: página 1 → cursor
  `c2t1ZG8xOjEwNA==`, página 2 → `[sku-alpha-lower, SKU-ÑOÑO, SKU-NORMAL]`) y en
  `/attributes` (3 páginas, 1.066 atributos, `next_cursor` avanza y termina en
  `null`).
- **UTF-8**: `SKU-ÑOÑO` (`53 4b 55 2d c3 91 4f c3 91 4f`) viaja intacto en la
  URL de respuesta, en un cuerpo JSON de POST, y hasta la fila del espejo.
- **`0000-00-00 00:00:00`**: llega como cadena y `parse_magento_datetime` lo
  convierte en `None`; `full_sync` lo reporta —`records_without_timestamp: 2`,
  `skus_without_timestamp: ["SKU-Z-LAST","SKU-Z-LAST"]`— en vez de abortar o
  inventar una fecha.
- **Procedencia de scope**: `price` (`is_global=2`) sale como `website` y `name`
  (`is_global=0`) como `store`; en el espejo, `SKU-ALPHA` tiene
  `proc_name=store, proc_price=website` en las dos tiendas.
- **`margin` por scope**: 0,4545 en la store 1 ((110−60)/110) y 0,3846 en la
  store 3 ((130−80)/130): resuelve el override de `cost` y de `price` por store
  view, no en global.
- **`search_demand`**: 12 y 0 en la store 1 (que tiene filas de `search_query`),
  `null` en la store 3 (que no tiene ninguna). No colapsa desconocido con cero.
- **`revenue: null`** para `SKU-BETA`, cuya única línea de pedido tiene
  `row_total_incl_tax NULL`: no reporta una cifra parcial con aspecto exacto.
- **`default_stock_id: null`** con MSI presente y dos stocks mapeados
  (`base→2`, `website_br→3`): responde "no sé" en vez de adivinar el stock 1.
- **`store_states` de categorías**: la categoría 10 sale activa en la store 1 e
  inactiva en la 3, con nombre distinto; la 11, cuyo override de `is_active` en
  la store 3 es **NULL**, hereda el global y sale activa (el arreglo de
  presencia-no-verdad funciona); la raíz absoluta, sin fila de `is_active`, sale
  inactiva con nombre resuelto.
- **`path` como lista de enteros**: `[1,2,10]`, `[1,99,12]`.
- **Los 105 tests PHPUnit siguen verdes** con el cambio de §6.1 —lo que confirma
  que la suite no cubre ese campo— y **los 185 tests Python** también.

---

## 6. Cambios que hubo que hacer para poder seguir

Dos. Los dos flagged aquí; ninguno es un arreglo de conveniencia.

### 6.1 `Model/ProductReader.php` — el arreglo de C1 (**afecta al módulo del repositorio**)

Sin esto `full_sync` no ingiere ni un producto (C1). El arreglo es el mínimo y es
exactamente la convención que `AttributeReader` ya aplica a `labels`:

```diff
-                'global_values' => $globalValues,
-                'store_values' => $stores[$key] ?? [],
+                // (object), no el array PHP: un mapa codigo -> valor VACIO
+                // (`[]`) es indistinguible de una lista para json_encode() y
+                // llega al cliente como `[]` en vez de `{}`. Es el MISMO bug
+                // que AttributeReader ya neutraliza en `labels` (ver el
+                // Ruling 2 de esa clase), aplicado al mapa de valores EAV: [...]
+                'global_values' => (object) $globalValues,
+                'store_values' => (object) ($stores[$key] ?? []),
```

Copia previa en `/home/ingmar/skudo-dev-logs/ProductReader.php.orig`.
Nota: **ningún test lo detectó** — los 105 siguen pasando. Falta cobertura, y
falta una aserción en `ServiceOutputEnvelopeTest` que fije la forma de estos dos
campos como ya lo hace con el envoltorio.

### 6.2 `app/code/NisseiStore/Custom/etc/acl.xml` — sólo en la copia de desarrollo

Ese módulo de terceros declara sus recursos ACL en minúsculas
(`nisseistore_custom::menu`), lo que **no valida contra el XSD de Magento**. En
modo `developer` la validación de esquema está activa, así que **el árbol ACL
completo falla al construirse**: `Acl\Builder::getAcl()` lanzaba
`LogicException`, `$acl->getResources()` volvía vacío, `grantPermissions()`
insertaba cero filas en `authorization_rule` y todos los endpoints daban
"El consumidor no está autorizado para acceder a Standard_Skudo::read".

En producción no se nota porque el modo `production` no valida el XSD.

Se renombraron los dos ids a `NisseiStore_Custom::…` en la copia, junto con sus
dos únicas referencias (`Plugin/Order.php`, `Controller/Adminhtml/Order/OrderAction.php`).
Tras eso: 466 recursos en el ACL y `Standard_Skudo::read` presente.
**El archivo de producción no se tocó** (sigue con `nisseistore_custom::`).
Copia previa en `/home/ingmar/skudo-dev-logs/acl.xml.orig`.

### 6.3 Dos ajustes en la base de desarrollo, no en código

- `DROP INDEX NISSEI_ACART_SWEEP ON quote` — un índice **funcional**
  (`ifnull(updated_at, created_at)`) que el `SchemaBuilder` declarativo de
  Magento no sabe leer: emitía `User Warning: Column does not exist for
  index/constraint NISSEI_ACART_SWEEP`, que en modo developer es fatal, y
  abortaba `setup:upgrade`. Es el único índice funcional del esquema. El de
  producción sigue en su sitio.
- `CREATE TABLE customer_grid_flat` con `created_in` como `varchar(255)` en vez
  de `text`. Antes de eso, el `recurring` de `Magento_Customer` generaba
  `INDEX(created_in)` sobre una columna `TEXT` y abortaba con el error 1170.
  **La causa era mi copia selectiva**, no producción: `customer_eav_attribute`
  estaba vacía, así que las banderas de grid de los atributos de cliente se
  leían como nulas. Copiar esa tabla lo resolvió de raíz; el `CREATE TABLE`
  quedó como red de seguridad.

---

## 7. Lo que reportó el arnés de aceptación

`python -m skudo.acceptance.s0 --tenant skudodev --stores 1,3`, sobre un espejo
recreado desde cero y una instancia con `setup:di:compile` hecho:

```
[OK ] espejo_sincronizado: sin deriva
[OK ] score_por_store_view: conteos por store view: {1: 7, 3: 7}
[OK ] procedencia_de_scope: 14 registros revisados; procedencias: {'store': 4, 'global': 61, 'website': 3}
[OK ] identidad_de_opciones: 327 opciones con etiqueta distinta por store view reconocidas como una sola opción
[OK ] efecto_de_categoria: 5 producto(s) con efecto de categoría distinto entre store views; 8 par(es) evaluado(s), 0 sin evaluar por website_desconocido, 0 par(es) sin website de la tienda espejado

EXIT=0
```

**Los cinco criterios pasan.** Es la primera vez que pasan contra un Magento
real. Vale la pena leer los detalles, no sólo los `OK`:

- `procedencia_de_scope` observó las **dos** escalas resueltas (`store` 4 y
  `website` 3), no sólo `global`, y ninguna `desconocido`. La guarda contra la
  vacuidad se satisfizo con datos, no por construcción.
- `identidad_de_opciones` reconoció 327 opciones con etiqueta distinta por store
  view — datos reales del catálogo de producción, no la opción sembrada.
- `efecto_de_categoria` evaluó 8 pares con `website_desconocido` en cero, y
  `derive_category_effect` discriminó entre tiendas para 5 productos, alimentado
  enteramente desde filas del espejo.
- `espejo_sincronizado` dice "sin deriva" y es **cierto y engañoso a la vez**:
  los dos lados coinciden en 7 productos porque los dos miran el mismo conjunto
  estrechado por C2. Los 8 que hay de verdad, no. Es la demostración de que el
  criterio 1 no puede detectar C2.

Las pasadas del ingestor, en orden, contra el Magento real:

```
sync_attributes  OK  {"pages_fetched": 3, "attributes_written": 1066, "options_written": 50535}
sync_categories  OK  {"pages_fetched": 1, "categories_written": 6}
full_sync        OK  {"records_written": 14, "records_deleted": 0, "pages_fetched": 2,
                      "records_without_timestamp": 2, "skus_without_timestamp": ["SKU-Z-LAST","SKU-Z-LAST"]}
sync_signals     OK  {"store_views_read": 2, "signals_written": 5}
delta_sync       OK  {"changes_seen": 5, "records_updated": 6, "records_deleted": 0, "watermark": 5,
                      "records_without_timestamp": 2, "skus_without_timestamp": ["SKU-Z-LAST","SKU-Z-LAST"]}
reconcile        OK  {1: {magento_count: 7, mirror_count: 7, digest_matches: True, needs_full_sync: False},
                      3: {magento_count: 7, mirror_count: 7, digest_matches: True, needs_full_sync: False}}
```

Segunda pasada de `delta_sync` (la que sí envía `sinceTimestamp`, con una fila
de cola nueva): `{"changes_seen": 1, "records_updated": 2, "watermark": 6}` —
un solo cambio, ninguna activación de versión (A3).

Y las dos suites:

```
uv run pytest -q                                  -> 185 passed
SKUDO_MAGENTO_ROOT=/home/ingmar/magento-skudo-dev \
  vendor/bin/phpunit -c phpunit.xml               -> OK (105 tests, 432 assertions)
```

Nota útil: `bootstrap.php` respeta `SKUDO_MAGENTO_ROOT`, así que
**`make test-php` ya no necesita leer la instalación de producción** — basta
apuntarlo a `/home/ingmar/magento-skudo-dev`. Hoy su default sigue siendo
`/var/www/casanissei.com/v248`.

---

## 8. Confirmación de que producción no se tocó

### 8.1 Sistema de archivos

```
$ find /var/www/casanissei.com -newermt '-8 hours' \( -type f -o -type d \)
(sin resultados)
```

Ni un archivo ni un directorio bajo `/var/www/casanissei.com/` fue creado ni
modificado. `app/etc/env.php` de producción conserva su mtime
(`jul 21 14:23`) y su md5 (`c25797f9552cf4a6996f6131ef15473d`).

Todas las lecturas de producción fueron: `cat`/`sed`/`grep`/`find` sobre
archivos, `rsync` **desde** el árbol, `du`, y `docker inspect`/`docker images`.

### 8.2 Base de datos `nisseicom`

```sql
SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA='nisseicom' AND TABLE_NAME LIKE 'standard_skudo%';   -> 0
SELECT COUNT(*) FROM nisseicom.integration WHERE name LIKE '%skudo%';      -> 0
SELECT COUNT(*) FROM nisseicom.eav_attribute_option WHERE option_id>=900001; -> 0
SELECT COUNT(*) FROM nisseicom.catalog_product_entity WHERE sku LIKE 'SKU-%'; -> 0
SELECT COUNT(*) FROM nisseicom.setup_module WHERE module='Standard_Skudo';  -> 0
SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='nisseicom'; -> 726
SELECT COUNT(*) FROM nisseicom.catalog_product_entity;                     -> 228881
SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA='nisseicom' AND INDEX_NAME='NISSEI_ACART_SWEEP';      -> 2
```

Sin tabla de cola de cambios, sin integración, sin nada sembrado, sin fila en
`setup_module`, mismas 726 tablas, mismos 228.881 productos, y el índice
funcional que borré de **mi** base sigue en la de ellos.

Contra `percona8_local` sólo se ejecutaron `SELECT`, `SHOW`, y
`mysqldump --no-data` / `mysqldump --no-create-info`, siempre con
`--single-transaction --skip-lock-tables` para no bloquear escrituras.
Cero `INSERT`/`UPDATE`/`DELETE`/`ALTER`/`CREATE`/`DROP`.

### 8.3 Contenedores y comandos

- Ningún `bin/magento` se ejecutó dentro de `v248`. Todos corrieron desde
  `/home/ingmar/magento-skudo-dev`.
- Ningún contenedor `*_local` se reinició, detuvo, reconfiguró ni recibió un
  `exec` de escritura. `percona8_local` sólo recibió `mysql`/`mysqldump` de
  lectura; `opensearch_local` no recibió nada.
- Ningún volumen de los contenedores de producción se montó. Los dos volúmenes
  nuevos (`skudo_dev_db_data`, `skudo_dev_os_data`) se crearon vacíos.
- `nginx_local` y `varnish_local` no se tocaron; el servidor propio escucha en
  `127.0.0.1:8088`.

---

## 9. Cómo desmontar el entorno

```bash
# 1. Parar el servidor HTTP
kill "$(cat /home/ingmar/skudo-dev-logs/php-server.pid)"

# 2. Borrar los contenedores y volúmenes propios
docker rm -f skudo_dev_db skudo_dev_opensearch
docker volume rm skudo_dev_db_data skudo_dev_os_data

# 3. Borrar la base del espejo (el contenedor de Postgres es preexistente;
#    NO lo borres, lo usa la suite de tests)
docker exec ecommerce_arp-postgres-1 \
  psql -U skudo -d postgres -c 'DROP DATABASE IF EXISTS skudo_httpreal;'

# 4. Quitar el enlace al módulo antes de borrar el árbol,
#    para no seguir el symlink hacia el repositorio
rm -f /home/ingmar/magento-skudo-dev/app/code/Standard/Skudo

# 5. Borrar el árbol de código y los artefactos
rm -rf /home/ingmar/magento-skudo-dev
rm -rf /home/ingmar/skudo-dev-logs

# 6. Revertir el arreglo de C1 en el módulo del repositorio, SI se decide
#    revertirlo (recomendación: no revertirlo — sin él el ingestor no funciona)
cd /home/ingmar/.gemini/antigravity/scratch/Ecommerce_Arp
git diff magento-module/Standard/Skudo/Model/ProductReader.php
# git checkout -- magento-module/Standard/Skudo/Model/ProductReader.php
```

El paso 4 es importante: `rm -rf` sobre el árbol sin quitar antes el symlink es
seguro (`rm` no lo sigue), pero quitarlo explícitamente evita cualquier duda.

Nada que borrar bajo `/var/www/casanissei.com/`, en la base `nisseicom` ni en
los contenedores `*_local`: no se creó nada ahí.

---

## 10. Qué queda pendiente

Por orden de gravedad, y ninguno arreglado en este ejercicio (salvo C1):

1. **C2 / A3** — decidir cuál es la regla de versión activa. Hoy hay dos
   compitiendo y su conjunción pierde 36 productos reales y mata el camino de
   `sinceTimestamp`. Las opciones son: quitar `ActiveVersionResolver` y confiar
   en Magento (pero entonces entran las versiones expiradas); usar
   `Magento\Framework\DB\UnversionedSelectFactory` (el virtualType que Staging
   provee para saltarse su propio filtro, `di.xml:156`) y quedarse sólo con la
   regla del reloj; o alinear la regla del módulo con el id de versión que
   `VersionManager` reporta. La decisión afecta a `/products`, `/products-by-sku`,
   `/checksums`, `/deltas`, `/signals` y `/categories` a la vez.
2. **A1 / A2** — `/signals` debe mirar el mismo conjunto de productos que
   `/products`, y `physical_qty` no debe llegar `null` cuando la fila de stock
   existe.
3. **C1** — añadir la aserción que falta. `ServiceOutputEnvelopeTest` fija la
   forma del envoltorio endpoint por endpoint; debería fijar también que
   `global_values`, `store_values` y `labels` serializan como objeto JSON con el
   mapa **vacío**, que es el caso que ninguno de los 105 tests toca.
4. **M1** — decidir si `counts.products` debe usar la misma regla que
   `/checksums` o renombrarse a algo que no invite a compararlos.
5. **M2 / M3** — `InputException` en vez de `InvalidArgumentException` en
   `Cursor::decode()`, y validar que la store view exista.
6. **B1** — documentar la variación `int`/`float` o forzar el tipo en PHP.
7. Cobertura: `FakeSelect::join()` sigue siendo un no-op, así que `eavValues()`,
   `websiteIds()` y `categoryIds()` continúan sin ejecutarse en la suite. Este
   entorno permite ahora escribir tests de integración reales contra
   `SKUDO_MAGENTO_ROOT=/home/ingmar/magento-skudo-dev`.
