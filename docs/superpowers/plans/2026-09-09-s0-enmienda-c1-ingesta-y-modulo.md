# S0 — Enmienda C1: ingesta de atributos y categorías, y el módulo PHP

> **Para trabajadores agénticos:** SUB-SKILL REQUERIDA: usar
> superpowers:subagent-driven-development para implementar este plan tarea por tarea.

**Goal:** Cerrar la laguna C1 de la revisión final —cuatro tablas del espejo sin camino
de ingesta— y construir el módulo Magento que las alimenta, para que los cuatro
criterios de aceptación de S0 sean alcanzables sobre un espejo real y no solo sobre
fixtures sembrados a mano.

**Architecture:** Se añaden dos endpoints al módulo (`/attributes` y `/categories`) y
sus dos ingestores en Python, más la columna de websites que `derive_category_effect`
necesita y la revocación de categorías en el camino incremental. Las cinco tareas PHP
del plan original quedan desbloqueadas y se ejecutan tal como están escritas.

**Spec:** `docs/superpowers/specs/2026-09-08-standardskudo-catalog-quality-design.md`
**Plan original:** `docs/superpowers/plans/2026-09-09-s0-conector-espejo-canonico.md`
**Revisión que la motiva:** `.superpowers/sdd/2026-09-09-s0-conector-espejo-canonico/final-review-findings.md` (hallazgo C1)

## Por qué existe esta enmienda

La revisión final encontró que `upsert_attribute`, `upsert_option`, `upsert_category` y
`set_category_store_state` se llaman **solo desde tests**, y que el plan original no
define ningún endpoint que pudiera alimentarlas. Dos consecuencias:

1. El criterio 4 del spec —una opción traducida PY/BR se reconoce como la misma
   `option_id`— **falla** sobre un espejo real, porque la tabla `attribute` está vacía.
   Pasa en los tests solo porque el fixture la siembra.
2. `derive_category_effect` necesita `Category.path`, `CategoryStoreState.is_active` y
   los `website_ids` del producto. **Ninguno de los tres se escribe nunca.** La función
   es correcta y es inalcanzable desde datos del espejo.

Es una laguna del plan, no un error de implementación: ninguna tarea fue nunca
encargada de escribir esos caminos.

## Global Constraints

Rigen todas las de este plan, además de las del plan original:

- **Nombre del módulo:** `Standard_Skudo`, namespace `Standard\Skudo\`, fuente en
  `magento-module/Standard/Skudo/` **dentro de este repositorio**.
- **La instancia Magento es de solo lectura.** Autorización del usuario: escribir el
  módulo y correr PHPUnit contra la instalación, **sin habilitarlo en la tienda y sin
  tocar la base de datos**. Prohibido: copiar o enlazar el módulo dentro de
  `app/code`, ejecutar `setup:upgrade`, `setup:di:compile`, `cache:flush`, cualquier
  `INSERT`/`UPDATE`/`DELETE`/`ALTER`, y cualquier comando `bin/magento` que escriba.
  Las lecturas `SELECT` para verificar un supuesto están permitidas.
- **Instancia de referencia** (verificada, solo lectura):
  `/var/www/casanissei.com/v248` — Adobe Commerce **Enterprise 2.4.8-p3**,
  `Magento_Staging` **activo** (por tanto `row_id`, no `entity_id`),
  `Magento_InventoryApi` activo, 228.881 productos, **422 attribute sets**,
  store views **1 (`py`)** y **3 (`br`)**, ambas con `root_category_id = 2`, en
  websites distintos (`base`, `website_br`). PHP 8.3.15, PHPUnit 10.5.60 en
  `vendor/bin/phpunit`, config en `dev/tests/unit/phpunit.xml.dist`.
- **El módulo no contiene lógica de calidad.** Solo mueve datos.
- **El módulo es de solo lectura en S0.** Ningún endpoint de escritura.
- Toda tabla del espejo lleva `tenant_id` y todo acceso lo exige.
- La identidad se guarda como texto, sin normalización numérica.
- Las opciones se identifican por `option_id`, nunca por etiqueta.
- Timestamps en UTC (`TIMESTAMPTZ`). Migraciones solo por Alembic, continuando la
  cadena desde `0010`.
- `ruff` con `line-length = 100` sobre `src tests`. Conocido y fuera de alcance: el
  I001 preexistente de `alembic/env.py`.

---

## Task A0: Arnés de PHPUnit sin tocar la instalación

Sin esto ninguna tarea PHP puede verificarse, y verificar es la condición que el usuario
puso para autorizar el trabajo.

**Files:**
- Create: `magento-module/phpunit.xml`
- Create: `magento-module/bootstrap.php`
- Create: `magento-module/Standard/Skudo/registration.php` (mínimo, para el autoload)
- Test: `magento-module/Standard/Skudo/Test/Unit/SmokeTest.php`

**Interfaces:**
- Consumes: nada
- Produces: un comando único y repetible que ejecuta los tests unitarios del módulo
  contra las clases del framework de la instalación, sin registrar el módulo en Magento.

- [ ] **Step 1: Escribir el bootstrap**

`bootstrap.php` carga el autoloader de la instalación (para que existan
`Magento\Framework\...`) y registra nuestro namespace por PSR-4, sin pasar por
`ComponentRegistrar`:

```php
<?php
declare(strict_types=1);

$magentoRoot = getenv('SKUDO_MAGENTO_ROOT') ?: '/var/www/casanissei.com/v248';
$autoload = $magentoRoot . '/vendor/autoload.php';

if (!is_file($autoload)) {
    fwrite(STDERR, "No se encontró el autoloader de Magento en $autoload\n");
    fwrite(STDERR, "Definí SKUDO_MAGENTO_ROOT apuntando a la raíz de la instalación.\n");
    exit(1);
}

require $autoload;

// El módulo NO se registra en Magento: solo se hace autocargable para los tests.
spl_autoload_register(static function (string $class): void {
    $prefix = 'Standard\\Skudo\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = substr($class, strlen($prefix));
    $path = __DIR__ . '/Standard/Skudo/' . str_replace('\\', '/', $relative) . '.php';
    if (is_file($path)) {
        require $path;
    }
});
```

- [ ] **Step 2: Escribir la configuración de PHPUnit**

```xml
<?xml version="1.0"?>
<phpunit xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:noNamespaceSchemaLocation="https://schema.phpunit.de/10.5/phpunit.xsd"
         bootstrap="bootstrap.php"
         colors="true"
         cacheDirectory=".phpunit.cache">
    <testsuites>
        <testsuite name="Standard_Skudo unit">
            <directory>Standard/Skudo/Test/Unit</directory>
        </testsuite>
    </testsuites>
</phpunit>
```

- [ ] **Step 3: Escribir el registration mínimo**

```php
<?php
declare(strict_types=1);

use Magento\Framework\Component\ComponentRegistrar;

ComponentRegistrar::register(ComponentRegistrar::MODULE, 'Standard_Skudo', __DIR__);
```

- [ ] **Step 4: Escribir el test que falla**

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit;

use Magento\Framework\App\ProductMetadataInterface;
use PHPUnit\Framework\TestCase;

class SmokeTest extends TestCase
{
    public function testMagentoFrameworkIsAutoloadable(): void
    {
        $this->assertTrue(
            interface_exists(ProductMetadataInterface::class),
            'el autoloader de la instalación Magento no está disponible'
        );
    }

    public function testModuleNamespaceIsAutoloadable(): void
    {
        $this->assertTrue(
            class_exists(\Standard\Skudo\Test\Unit\SmokeTest::class),
            'el namespace del módulo no es autocargable'
        );
    }
}
```

- [ ] **Step 5: Ejecutar y comprobar que falla**

Run, desde el contenedor PHP para tener la misma versión que la instalación:

```bash
docker exec php83_default_local sh -lc \
  'cd /home/ingmar/.gemini/antigravity/scratch/Ecommerce_Arp/magento-module && \
   /var/www/casanissei.com/v248/vendor/bin/phpunit -c phpunit.xml'
```

Si el directorio del repo no está montado en el contenedor, ejecutar en el host con el
PHP del sistema (8.3.6), que es compatible:

```bash
cd magento-module && /var/www/casanissei.com/v248/vendor/bin/phpunit -c phpunit.xml
```

Expected antes de escribir el bootstrap: fallo por autoloader ausente.

- [ ] **Step 6: Implementar y confirmar que pasa**

Expected: 2 passed.

- [ ] **Step 7: Añadir el comando al Makefile**

```makefile
test-php:
	cd magento-module && $(MAGENTO_PHPUNIT) -c phpunit.xml

MAGENTO_PHPUNIT ?= /var/www/casanissei.com/v248/vendor/bin/phpunit
```

- [ ] **Step 8: Commit**

```bash
git add magento-module/ Makefile
git commit -m "chore(s0): arnés de PHPUnit para el módulo sin registrarlo en Magento"
```

---

## Tareas del plan original que quedan desbloqueadas

Las siguientes se ejecutan **tal como están escritas** en
`docs/superpowers/plans/2026-09-09-s0-conector-espejo-canonico.md`, en este orden, con
dos correcciones globales que la instancia real impone:

1. **La clave de entidad es `row_id`**, porque `Magento_Staging` está activo. El código
   ya la descubre por sonda, así que no hay cambio de diseño; pero cualquier consulta
   que un implementador escriba a mano debe usar la columna que devuelve la sonda.
2. **El comando de PHPUnit** es el del Task A0, no el
   `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist` que dicen los briefs — ese
   registraría el módulo en la suite de la instalación, que es justo lo que no se hace.

- **Task 3** — sonda de entorno (`EnvironmentProbe`). Su test verifica que la clave se
  detecta del esquema y no de la edición: en esta instancia Enterprise **con** Staging
  debe devolver `row_id`, y el caso Enterprise **sin** Staging debe seguir devolviendo
  `entity_id`.
- **Task 8** — lectura masiva por cursor (`Cursor`, `ProductReader`). Aplica el ruling de
  pre-flight nº4: extraer `Model\EntityKeyResolver` con `resolve(): string` memoizado,
  usado por `EnvironmentProbe` y por `ProductReader`, en lugar de llamar a
  `getProfile()` por página — `getProfile()` ejecuta cuatro `COUNT(*)` y en 228.881
  productos serían cientos de conteos de tabla completa.
- **Task 10** — cola de cambios y deltas (`ChangeLog`, `ProductChanged`, `DeltaReader`).
- **Task 12 (mitad PHP)** — `SignalReader` con `usesMsi` y el fallback de inventario.
- **Task 13 (mitad PHP)** — `ChecksumReader`. **Contrato crítico:** debe ordenar en PHP
  con `sort($skus, SORT_STRING)` y **nunca** con `ORDER BY` de SQL, que bajo
  `utf8mb4_general_ci` produce otro orden para catálogos con mayúsculas mezcladas o
  acentos. Y `product_count` debe contar los productos **visibles en esa store view**,
  no un `COUNT(*)` global, o produce deriva falsa permanente en un tenant multi-website.

Además, el endpoint `/products-by-sku` que la Task 11 dejó pendiente se implementa aquí
con el contrato que impuso F6 de la ola de arreglos: **POST con cuerpo JSON y lotes de
como máximo 100 SKUs**, no un GET con SKUs unidos por comas. El reporte de la ola de
arreglos (`final-fix-report.md`) tiene la forma exacta que el cliente Python ya espera.

---

## Task A1: Endpoint de atributos con sus opciones y etiquetas por store view

**Files:**
- Create: `magento-module/Standard/Skudo/Api/AttributeReaderInterface.php`
- Create: `magento-module/Standard/Skudo/Model/AttributeReader.php`
- Modify: `magento-module/Standard/Skudo/etc/webapi.xml`, `etc/di.xml`
- Test: `magento-module/Standard/Skudo/Test/Unit/Model/AttributeReaderTest.php`

**Interfaces:**
- Consumes: `EntityKeyResolver` (Task 8)
- Produces: `GET /rest/V1/skudo/attributes?cursor=&limit=` devolviendo
  `{"items": [{code, label, frontend_input, declared_scope, is_filterable, is_required, attribute_set_ids, options: [{option_id, labels: {store_id: label}}]}], "next_cursor": ...}`

**Puntos que deciden si esto sirve o no:**

- `declared_scope` se deriva de `catalog_eav_attribute.is_global`: `1` → `"global"`,
  `2` → `"website"`, `0` → `"store"`. Esa es la fuente del tercer valor de procedencia
  que el hallazgo I1 necesita y que hoy no existe.
- Las etiquetas de opción salen de `eav_attribute_option_value`, que lleva `store_id`.
  Hay que devolver **todas** las filas por `option_id`, incluida la de `store_id = 0`
  (la etiqueta admin). Devolver solo la etiqueta de una tienda destruiría la identidad
  que la Task 5 protege.
- Hay 422 attribute sets y 612 atributos en la instancia real: el endpoint pagina por
  cursor sobre `attribute_id`, igual que los productos.
- `attribute_set_ids` se obtiene de `eav_entity_attribute`, y es lo que permite decidir
  si un atributo **aplica** a un producto — el `no_aplica` del eje 3.

---

## Task A2: Ingestor de atributos y opciones

**Files:**
- Create: `src/skudo/ingest/attribute_sync.py`
- Modify: `src/skudo/magento/client.py` (añadir `iter_attributes`)
- Test: `tests/ingest/test_attribute_sync.py`

**Interfaces:**
- Consumes: `upsert_attribute`, `upsert_option` (Task 5)
- Produces: `sync_attributes(session, client, tenant_id) -> AttributeSyncReport`

**Puntos que deciden si esto sirve o no:**

- El test que cierra el criterio 4 del spec: un atributo `select` cuyas opciones traen
  etiquetas distintas por store view debe acabar en el espejo como **una** `option_id`
  con dos etiquetas — y el arnés de aceptación debe pasar ese criterio **sin fixture
  sembrado a mano**.
- `upsert_option` reemplaza las etiquetas de la opción por completo, así que el
  ingestor debe mandar el mapa entero de etiquetas en una sola llamada por opción, no
  una llamada por etiqueta.

---

## Task A3: Endpoint de categorías con path y estado por tienda

**Files:**
- Create: `magento-module/Standard/Skudo/Api/CategoryReaderInterface.php`
- Create: `magento-module/Standard/Skudo/Model/CategoryReader.php`
- Modify: `magento-module/Standard/Skudo/etc/webapi.xml`, `etc/di.xml`
- Test: `magento-module/Standard/Skudo/Test/Unit/Model/CategoryReaderTest.php`

**Interfaces:**
- Produces: `GET /rest/V1/skudo/categories?cursor=&limit=` devolviendo
  `{"items": [{category_id, path: [int], default_name, store_states: [{store_id, is_active, name}]}], "next_cursor": ...}`

**Puntos que deciden si esto sirve o no:**

- `path` viene de `catalog_category_entity.path`, que Magento guarda como `"1/2/15"`:
  hay que devolverlo como lista de enteros, porque así lo consume
  `derive_category_effect` sin recursión.
- `is_active` y `name` se leen por store view del EAV de categoría, con el mismo patrón
  global/override que los productos: fila con `store_id = N` si hay override, y la de
  `store_id = 0` si no.
- En esta instancia **ambas store views cuelgan de la misma root category (2)**, así que
  el test debe cubrir el caso que sí discrimina: una categoría activa en PY e inactiva
  en BR.

---

## Task A4: Ingestor de categorías, y los websites del producto

**Files:**
- Create: `src/skudo/ingest/category_sync.py`
- Modify: `src/skudo/magento/client.py` (añadir `iter_categories`)
- Modify: `src/skudo/mirror/models.py` (añadir `website_ids` a `ProductRecord`)
- Modify: `src/skudo/mirror/products.py`, `src/skudo/ingest/full_sync.py`,
  `src/skudo/ingest/delta_sync.py`
- Create: `alembic/versions/0011_product_website_ids.py`
- Test: `tests/ingest/test_category_sync.py`, `tests/mirror/test_category_effect_real.py`

**Interfaces:**
- Consumes: `upsert_category`, `set_category_store_state`, `derive_category_effect` (Task 7)
- Produces: `sync_categories(session, client, tenant_id, store_view_ids) -> CategorySyncReport`;
  `ProductRecord.website_ids` (JSON)

**Puntos que deciden si esto sirve o no:**

- **`website_ids` es la pieza que falta.** El payload de producto ya lo trae desde la
  Task 8 y `full_sync` lo tira a la basura; sin él `derive_category_effect` no puede
  evaluar su tercera condición, que es **justamente la que discrimina PY de BR en este
  tenant**. Añadir la columna, leerla en los dos caminos de sincronización.
- El test `test_category_effect_real` debe montar la topología real —dos store views,
  misma root category, websites distintos— y demostrar que el efecto se decide por
  website y por `is_active`, no por el árbol.

---

## Task A5: `delta_sync` revoca categorías

**Files:**
- Modify: `src/skudo/ingest/delta_sync.py`
- Test: `tests/ingest/test_delta_sync.py`

Spillover reconocido por la ola de arreglos: `full_sync` ya reemplaza el conjunto de
asignaciones (F2), pero `delta_sync` ignora `category_ids` del payload, así que una
categoría que un producto abandonó no se revoca hasta el siguiente full sync. Con un
registrador que reescribe productos constantemente, eso son asignaciones fantasma
acumulándose entre pasadas completas.

*Aceptación:* un producto refrescado por delta con una categoría menos queda con una
categoría menos en el espejo.

---

## Task A6: Los criterios de aceptación, ahora alcanzables

**Files:**
- Modify: `src/skudo/acceptance/s0.py`
- Modify: `docs/superpowers/plans/s0-verificacion-manual.md`
- Modify: `README.md`
- Test: `tests/acceptance/test_s0_harness.py`

- Añadir un quinto criterio, `efecto_de_categoria`, que compruebe que hay datos de
  categoría en el espejo y que `derive_category_effect` produce efectos distintos entre
  las dos store views para al menos un producto. El criterio 3 del spec pide "efecto de
  categoría verificado", y hasta ahora solo existía en la checklist manual.
- Reescribir el test de "healthy mirror" para que **no siembre a mano** las tablas de
  atributos y opciones: debe poblarlas por los ingestores nuevos. Ese fixture sembrado
  es la razón por la que C1 sobrevivió once revisiones, y mientras siga ahí el arnés
  puede volver a mentir.
- `README.md` sigue diciendo "En diseño. No hay código todavía." y contiene una errata,
  "StandardStandardSkudo". El repositorio es **público**: corregir ambas.

*Aceptación:* `python -m skudo.acceptance.s0 --tenant nissei --stores 1,3` pasa los
cinco criterios contra un espejo poblado únicamente por los ingestores.

---

## Task A7: Las dos brechas residuales de la ola de arreglos

La re-revisión del fix wave dio todas las F1–F15 como resueltas, con dos residuos contra
la letra del hallazgo. No merecían una segunda ola, y sí merecen cerrarse aquí.

**Files:**
- Modify: `src/skudo/magento/source.py`
- Modify: `pyproject.toml`
- Test: `tests/magento/test_source.py`, `tests/ingest/test_delta_sync.py`,
  `tests/ingest/test_full_sync.py`

**A7.1 — `TenantSource` no impide todavía el par mal emparejado.** F8 cerró el riesgo
principal: los cuatro puntos de entrada ya no reciben un `tenant_id` suelto, así que un
llamador no puede cruzar tenant y credenciales por descuido. Pero
`TenantSource.__init__` sigue aceptando `tenant_id`, `base_url` y `token` de forma
independiente, así que `TenantSource(tenant_id=B, base_url=A_url, token=A_token)` sigue
siendo construible; solo `from_tenant()` los ata. Hacer que `from_tenant()` sea la única
vía: `__init__` privado por convención explícita —o el objeto congelado y construido
solo desde la fila del tenant— y un test que compruebe que la construcción directa con
un par cruzado falla.

**A7.2 — los tests de guarda de avance pueden colgarse en vez de fallar.** F11 añadió las
guardas y sus tests, y la ola reportó que antes del arreglo esos tests **se colgaban** en
lugar de fallar. Hoy no hay `pytest-timeout` ni límite de iteraciones, así que si una
guarda regresa, el test vuelve a colgarse indefinidamente en lugar de reportar. Un test
que se cuelga es peor que un test que falla: no dice nada y bloquea la suite. Añadir
`pytest-timeout` como dependencia de desarrollo y un timeout acotado en los dos tests de
guarda.

*Aceptación:* construir un `TenantSource` con tenant y credenciales cruzados falla; y si
se revierte una guarda de avance a mano, su test **falla por timeout** en segundos en vez
de colgar la suite.

---

## Riesgos de esta enmienda

| Riesgo | Mitigación |
|---|---|
| Tocar una instalación llamada `*-production` | Autorización explícita y limitada: módulo fuera de `app/code`, PHPUnit con arnés propio, ningún comando de escritura, ninguna migración |
| `row_id` frente a `entity_id` en consultas escritas a mano | La sonda es la única fuente; `EntityKeyResolver` memoizado la centraliza |
| El digest se ordena distinto en PHP que en Python | `sort($skus, SORT_STRING)` obligatorio, nunca `ORDER BY`; y la comprobación manual queda en la checklist |
| 422 attribute sets desbordan la curación de reglas de S1 | Fuera del alcance de esta enmienda, pero el criterio de aceptación de S1 en el spec debe replantearse antes de planificar S1 |
| El arnés vuelve a pasar por fixtures sembrados | Task A6 elimina el sembrado a mano del test de "healthy mirror" |
