# S0 — Conector y Espejo Canónico · Plan de Implementación

> **Para trabajadores agénticos:** SUB-SKILL REQUERIDA: usar
> superpowers:subagent-driven-development (recomendado) o
> superpowers:executing-plans para implementar este plan tarea por tarea. Los
> pasos usan sintaxis de checkbox (`- [ ]`) para seguimiento.

**Goal:** Mantener en Postgres un espejo canónico y fresco del catálogo Magento de un
tenant, por store view, con procedencia de scope, identidad desglosada e identidad de
opciones — alimentado por un módulo Magento propio mediante carga masiva y deltas.

**Architecture:** Un módulo PHP en el Magento del cliente expone endpoints REST de solo
lectura (sonda de entorno, lectura masiva por cursor, deltas desde watermark, señales
comerciales, checksums). Un ingestor Python por tenant los consume y hace upsert en el
espejo. El espejo separa lo que Magento guarda global de su efecto por store view. La
sonda de entorno se ejecuta primero y **el resto del sistema se adapta a lo que
descubre** en lugar de asumir edición, versión o esquema.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 · Alembic · Postgres 16 · httpx · pytest ·
uv · PHP 8.2 · Magento 2.4.x · PHPUnit

**Spec:** `docs/superpowers/specs/2026-09-08-standardskudo-catalog-quality-design.md`

## Global Constraints

- **Nombre del módulo Magento:** `Standard_Skudo`, en `app/code/Standard/Skudo`. Sigue
  la convención de la familia (`Standard_SemanticSearch` ya existe en producción).
- **El módulo no contiene lógica de calidad.** Solo mueve datos. Ninguna regla, ningún
  score, ninguna decisión de negocio en PHP.
- **S0 es de solo lectura.** No se implementa ningún endpoint de escritura. El
  write-back pertenece a S3, donde algo lo consume (YAGNI). Esto es coherente con los
  criterios de aceptación de S0 en el spec, que no mencionan escritura.
- **El objeto central es `(tenant, producto, store_view)`.** Ninguna tabla de registro
  evaluable puede tener clave solo `(tenant, producto)`.
- **La asignación producto–categoría es global** en el core de Magento
  (`catalog_category_product` no tiene `store_id`). El efecto por store view se
  **deriva**; nunca se guarda como si fuera una asignación.
- **La identidad de opciones es `option_id`**, nunca la etiqueta. `Negro` y `Preto`
  pueden ser la misma opción traducida.
- **La identidad de producto se guarda desglosada**: `sku` interno, `mpn`, `modelo`,
  `gtin`, identidad de variante. Se preservan ceros iniciales y sufijos como **texto**;
  ninguna normalización numérica.
- **Clave de entidad descubierta, no asumida.** Adobe Commerce con Staging usa `row_id`
  en `catalog_product_entity`; Open Source usa `entity_id`. Toda consulta usa la columna
  que devuelve la sonda.
- **Disponibilidad vendible**, no cantidad física, cuando MSI esté activo.
- **Los secretos nunca en Postgres ni en el repo.** El token de integración de cada
  tenant se lee de variable de entorno `SKUDO_TENANT_<CODE>_TOKEN`; la base guarda solo
  el nombre de la variable.
- **Aislamiento por tenant:** toda tabla del espejo lleva `tenant_id` y todo acceso pasa
  por un repositorio que lo exige como parámetro. Ninguna consulta sin `tenant_id`.
- **Migraciones solo por Alembic.** Ningún DDL a mano.
- **Zona horaria:** todo timestamp se guarda en UTC (`TIMESTAMPTZ`).

---

## Estructura de archivos

**Ingestor y espejo (Python)**

| Archivo | Responsabilidad |
|---|---|
| `pyproject.toml` | dependencias y configuración de herramientas |
| `docker-compose.yml` | Postgres local para desarrollo y tests |
| `alembic.ini`, `alembic/env.py` | configuración de migraciones |
| `alembic/versions/*.py` | una migración por tarea de esquema |
| `src/skudo/config.py` | configuración desde entorno; resolución de secretos |
| `src/skudo/magento/environment.py` | `EnvironmentProfile` y su parser |
| `src/skudo/magento/client.py` | cliente HTTP del módulo, paginación por cursor |
| `src/skudo/mirror/models.py` | modelos SQLAlchemy del espejo |
| `src/skudo/mirror/topology.py` | tiendas, websites, grupos, root categories |
| `src/skudo/mirror/attributes.py` | attribute sets, atributos, opciones y labels |
| `src/skudo/mirror/products.py` | registros de producto y procedencia de scope |
| `src/skudo/mirror/categories.py` | categorías, asignación global, derivación de efecto |
| `src/skudo/mirror/signals.py` | señales comerciales |
| `src/skudo/ingest/full_sync.py` | carga completa |
| `src/skudo/ingest/delta_sync.py` | sincronización incremental y watermark |
| `src/skudo/ingest/reconcile.py` | checksums y detección de deriva |
| `src/skudo/acceptance/s0.py` | arnés de los criterios de aceptación de S0 |

**Módulo Magento (PHP)** — todo bajo `magento-module/Standard/Skudo/`

| Archivo | Responsabilidad |
|---|---|
| `registration.php`, `etc/module.xml`, `composer.json` | declaración del módulo |
| `etc/webapi.xml`, `etc/acl.xml`, `etc/di.xml` | rutas, permisos, preferencias |
| `etc/db_schema.xml` | tabla de cola de cambios |
| `Api/*Interface.php` | contratos de los endpoints |
| `Model/EnvironmentProbe.php` | edición, versión, esquema, topología |
| `Model/ProductReader.php` | lectura masiva por cursor |
| `Model/Cursor.php` | codificación y decodificación del cursor |
| `Model/ChangeLog.php`, `Observer/*.php` | cola de cambios |
| `Model/DeltaReader.php` | deltas desde watermark |
| `Model/SignalReader.php` | señales comerciales, con fallback MSI |
| `Model/ChecksumReader.php` | checksums por store view |
| `Test/Unit/**` | PHPUnit |

---

## Task 1: Andamiaje del proyecto

**Files:**
- Create: `pyproject.toml`, `docker-compose.yml`, `Makefile`, `alembic.ini`
- Create: `src/skudo/__init__.py`, `src/skudo/config.py`
- Create: `alembic/env.py`, `alembic/script.py.mako`
- Test: `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nada
- Produces: `skudo.config.Settings` con atributos `database_url: str`,
  `tenant_token(code: str) -> str`; fixture pytest `db_session` que entrega una
  `sqlalchemy.orm.Session` sobre una base migrada; fixture `settings`.

- [ ] **Step 1: Crear `pyproject.toml`**

```toml
[project]
name = "skudo"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "sqlalchemy>=2.0.30",
  "alembic>=1.13",
  "psycopg[binary]>=3.1",
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "pytest-asyncio>=0.23", "ruff>=0.5"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/skudo"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Crear `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: skudo
      POSTGRES_PASSWORD: skudo
      POSTGRES_DB: skudo
    ports: ["55432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U skudo"]
      interval: 2s
      timeout: 3s
      retries: 20
```

- [ ] **Step 3: Escribir el test que falla**

```python
# tests/test_config.py
import pytest
from skudo.config import Settings

def test_database_url_comes_from_environment(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    assert Settings().database_url == "postgresql+psycopg://u:p@h:5432/d"

def test_tenant_token_reads_per_tenant_env_var(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.setenv("SKUDO_TENANT_NISSEI_TOKEN", "abc123")
    assert Settings().tenant_token("nissei") == "abc123"

def test_missing_tenant_token_is_an_error(monkeypatch):
    monkeypatch.setenv("SKUDO_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    monkeypatch.delenv("SKUDO_TENANT_GHOST_TOKEN", raising=False)
    with pytest.raises(KeyError):
        Settings().tenant_token("ghost")
```

- [ ] **Step 4: Ejecutar el test y comprobar que falla**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.config'`

- [ ] **Step 5: Implementar `src/skudo/config.py`**

```python
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración del ingestor. Los secretos nunca viven en la base."""

    model_config = SettingsConfigDict(env_prefix="SKUDO_")

    database_url: str

    def tenant_token(self, tenant_code: str) -> str:
        var = f"SKUDO_TENANT_{tenant_code.upper()}_TOKEN"
        try:
            return os.environ[var]
        except KeyError as exc:
            raise KeyError(f"falta la variable de entorno {var}") from exc
```

- [ ] **Step 6: Ejecutar el test y comprobar que pasa**

Run: `uv run pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 7: Inicializar Alembic y el fixture de base de datos**

Run: `uv run alembic init -t generic alembic`

Después reemplazar el bloque de configuración de `alembic/env.py` para que lea la URL
del entorno:

```python
# alembic/env.py — sustituir la línea config.set_main_option / get_url por esto
import os
from skudo.mirror.models import Base  # se crea en la Task 4

config.set_main_option("sqlalchemy.url", os.environ["SKUDO_DATABASE_URL"])
target_metadata = Base.metadata
```

Y crear el conftest:

```python
# tests/conftest.py
import os
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_URL = os.environ.get(
    "SKUDO_TEST_DATABASE_URL", "postgresql+psycopg://skudo:skudo@localhost:55432/skudo"
)


@pytest.fixture(scope="session")
def migrated_engine():
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    os.environ["SKUDO_DATABASE_URL"] = TEST_URL
    command.upgrade(Config("alembic.ini"), "head")
    return engine


@pytest.fixture
def db_session(migrated_engine):
    """Sesión con rollback al final: cada test queda aislado."""
    conn = migrated_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn)
    yield session
    session.close()
    trans.rollback()
    conn.close()
```

> Nota: `alembic/env.py` importa `Base` de la Task 4. Hasta que esa tarea exista, el
> import falla. Por eso este paso deja `alembic/env.py` escrito pero **el fixture
> `migrated_engine` no se usa** en ningún test hasta la Task 4. `tests/test_config.py`
> no lo toca.

- [ ] **Step 8: Crear el `Makefile`**

```makefile
up:
	docker compose up -d --wait

test: up
	uv run pytest -v

lint:
	uv run ruff check src tests

migrate: up
	SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo \
		uv run alembic upgrade head
```

- [ ] **Step 9: Verificar que todo el andamiaje funciona**

Run: `make up && uv run pytest tests/test_config.py -v && uv run ruff check src tests`
Expected: 3 passed, ruff sin hallazgos

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml docker-compose.yml Makefile alembic.ini alembic/ src/skudo/ tests/
git commit -m "chore(s0): andamiaje del ingestor con Postgres, Alembic y pytest"
```

---

## Task 2: Perfil de entorno — contrato y parser

Esta tarea define **qué necesitamos saber del Magento del tenant** antes de leer nada.
Es la que convierte la pregunta abierta "¿qué versión y edición?" en una medición.

**Files:**
- Create: `src/skudo/magento/__init__.py`, `src/skudo/magento/environment.py`
- Create: `tests/fixtures/environment_opensource.json`,
  `tests/fixtures/environment_commerce_staging.json`
- Test: `tests/magento/test_environment.py`

**Interfaces:**
- Consumes: nada
- Produces:
  - `EnvironmentProfile` (pydantic) con campos `edition: str`, `version: str`,
    `product_entity_key: Literal["entity_id","row_id"]`, `staging_enabled: bool`,
    `msi_enabled: bool`, `default_stock_id: int | None`, `websites: list[Website]`,
    `store_groups: list[StoreGroup]`, `store_views: list[StoreView]`,
    `counts: dict[str,int]`, `module_version: str`
  - `Website(id:int, code:str, name:str)`
  - `StoreGroup(id:int, website_id:int, code:str, name:str, root_category_id:int)`
  - `StoreView(id:int, group_id:int, code:str, name:str, is_active:bool, locale:str, currency:str)`
  - `parse_environment(payload: dict) -> EnvironmentProfile`
  - `EnvironmentProfile.store_view(store_id:int) -> StoreView`
  - `EnvironmentProfile.root_category_id_for(store_id:int) -> int`

- [ ] **Step 1: Crear los fixtures**

```json
// tests/fixtures/environment_opensource.json
{
  "edition": "Community",
  "version": "2.4.7-p3",
  "product_entity_key": "entity_id",
  "staging_enabled": false,
  "msi_enabled": true,
  "default_stock_id": 1,
  "websites": [{"id": 1, "code": "base", "name": "Main Website"}],
  "store_groups": [
    {"id": 1, "website_id": 1, "code": "py", "name": "Paraguay", "root_category_id": 2},
    {"id": 2, "website_id": 1, "code": "br", "name": "Brasil", "root_category_id": 3}
  ],
  "store_views": [
    {"id": 1, "group_id": 1, "code": "py_es", "name": "PY Español",
     "is_active": true, "locale": "es_PY", "currency": "PYG"},
    {"id": 2, "group_id": 2, "code": "br_pt", "name": "BR Português",
     "is_active": true, "locale": "pt_BR", "currency": "BRL"}
  ],
  "counts": {"products": 214883, "attribute_sets": 97, "attributes": 612, "categories": 1840},
  "module_version": "1.0.0"
}
```

```json
// tests/fixtures/environment_commerce_staging.json
{
  "edition": "Enterprise",
  "version": "2.4.7-p3",
  "product_entity_key": "row_id",
  "staging_enabled": true,
  "msi_enabled": true,
  "default_stock_id": 1,
  "websites": [{"id": 1, "code": "base", "name": "Main Website"}],
  "store_groups": [
    {"id": 1, "website_id": 1, "code": "py", "name": "Paraguay", "root_category_id": 2}
  ],
  "store_views": [
    {"id": 1, "group_id": 1, "code": "py_es", "name": "PY Español",
     "is_active": true, "locale": "es_PY", "currency": "PYG"}
  ],
  "counts": {"products": 214883, "attribute_sets": 97, "attributes": 612, "categories": 1840},
  "module_version": "1.0.0"
}
```

- [ ] **Step 2: Escribir los tests que fallan**

```python
# tests/magento/test_environment.py
import json
from pathlib import Path

import pytest

from skudo.magento.environment import parse_environment

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_opensource_uses_entity_id():
    profile = parse_environment(load("environment_opensource.json"))
    assert profile.product_entity_key == "entity_id"
    assert profile.staging_enabled is False


def test_commerce_with_staging_uses_row_id():
    profile = parse_environment(load("environment_commerce_staging.json"))
    assert profile.product_entity_key == "row_id"
    assert profile.staging_enabled is True


def test_root_category_is_resolved_through_the_store_group():
    """El store view no conoce su root category: la hereda del grupo."""
    profile = parse_environment(load("environment_opensource.json"))
    assert profile.root_category_id_for(1) == 2
    assert profile.root_category_id_for(2) == 3


def test_unknown_store_view_is_an_error():
    profile = parse_environment(load("environment_opensource.json"))
    with pytest.raises(KeyError):
        profile.store_view(999)


def test_staging_without_row_id_is_rejected_as_inconsistent():
    """Un Magento con Staging tiene que exponer row_id. Si no, la sonda mintió."""
    payload = load("environment_commerce_staging.json")
    payload["product_entity_key"] = "entity_id"
    with pytest.raises(ValueError, match="staging"):
        parse_environment(payload)
```

- [ ] **Step 3: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/magento/test_environment.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.magento'`

- [ ] **Step 4: Implementar `src/skudo/magento/environment.py`**

```python
from typing import Literal

from pydantic import BaseModel, model_validator


class Website(BaseModel):
    id: int
    code: str
    name: str


class StoreGroup(BaseModel):
    id: int
    website_id: int
    code: str
    name: str
    root_category_id: int


class StoreView(BaseModel):
    id: int
    group_id: int
    code: str
    name: str
    is_active: bool
    locale: str
    currency: str


class EnvironmentProfile(BaseModel):
    """Lo que el sistema descubrió del Magento del tenant.

    Nada aquí se asume: todo lo reporta la sonda del módulo.
    """

    edition: str
    version: str
    product_entity_key: Literal["entity_id", "row_id"]
    staging_enabled: bool
    msi_enabled: bool
    default_stock_id: int | None
    websites: list[Website]
    store_groups: list[StoreGroup]
    store_views: list[StoreView]
    counts: dict[str, int]
    module_version: str

    @model_validator(mode="after")
    def staging_implies_row_id(self):
        if self.staging_enabled and self.product_entity_key != "row_id":
            raise ValueError(
                "staging activo pero la clave de entidad es entity_id: "
                "la sonda es inconsistente y no se puede confiar en ella"
            )
        return self

    def store_view(self, store_id: int) -> StoreView:
        for view in self.store_views:
            if view.id == store_id:
                return view
        raise KeyError(f"store view desconocida: {store_id}")

    def root_category_id_for(self, store_id: int) -> int:
        group_id = self.store_view(store_id).group_id
        for group in self.store_groups:
            if group.id == group_id:
                return group.root_category_id
        raise KeyError(f"store group desconocido: {group_id}")


def parse_environment(payload: dict) -> EnvironmentProfile:
    return EnvironmentProfile.model_validate(payload)
```

- [ ] **Step 5: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/magento/test_environment.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/skudo/magento/ tests/magento/ tests/fixtures/
git commit -m "feat(s0): perfil de entorno del tenant con clave de entidad descubierta"
```

---

## Task 3: Módulo Magento — endpoint de sonda de entorno

**Files:**
- Create: `magento-module/Standard/Skudo/registration.php`
- Create: `magento-module/Standard/Skudo/composer.json`
- Create: `magento-module/Standard/Skudo/etc/module.xml`
- Create: `magento-module/Standard/Skudo/etc/acl.xml`
- Create: `magento-module/Standard/Skudo/etc/webapi.xml`
- Create: `magento-module/Standard/Skudo/etc/di.xml`
- Create: `magento-module/Standard/Skudo/Api/EnvironmentProbeInterface.php`
- Create: `magento-module/Standard/Skudo/Model/EnvironmentProbe.php`
- Test: `magento-module/Standard/Skudo/Test/Unit/Model/EnvironmentProbeTest.php`

**Interfaces:**
- Consumes: nada
- Produces: `GET /rest/V1/skudo/environment` devolviendo el payload que
  `parse_environment` (Task 2) valida. ACL `Standard_Skudo::read`.

- [ ] **Step 1: Declarar el módulo**

```php
<?php
// registration.php
declare(strict_types=1);

use Magento\Framework\Component\ComponentRegistrar;

ComponentRegistrar::register(ComponentRegistrar::MODULE, 'Standard_Skudo', __DIR__);
```

```xml
<!-- etc/module.xml -->
<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:framework:Module/etc/module.xsd">
    <module name="Standard_Skudo" setup_version="1.0.0">
        <sequence>
            <module name="Magento_Catalog"/>
            <module name="Magento_Store"/>
        </sequence>
    </module>
</config>
```

```json
// composer.json
{
  "name": "standard/module-skudo",
  "description": "Conector de solo lectura para StandardSkudo",
  "type": "magento2-module",
  "license": "proprietary",
  "require": {"php": "~8.2.0 || ~8.3.0"},
  "autoload": {
    "files": ["registration.php"],
    "psr-4": {"Standard\\Skudo\\": ""}
  }
}
```

- [ ] **Step 2: Declarar ACL y ruta**

```xml
<!-- etc/acl.xml -->
<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:framework:Acl/etc/acl.xsd">
    <acl>
        <resources>
            <resource id="Magento_Backend::admin">
                <resource id="Standard_Skudo::read" title="StandardSkudo: lectura de catálogo"/>
            </resource>
        </resources>
    </acl>
</config>
```

```xml
<!-- etc/webapi.xml -->
<?xml version="1.0"?>
<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:module:Magento_Webapi:etc/webapi.xsd">
    <route url="/V1/skudo/environment" method="GET">
        <service class="Standard\Skudo\Api\EnvironmentProbeInterface" method="getProfile"/>
        <resources><resource ref="Standard_Skudo::read"/></resources>
    </route>
</routes>
```

```xml
<!-- etc/di.xml -->
<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:framework:ObjectManager/etc/config.xsd">
    <preference for="Standard\Skudo\Api\EnvironmentProbeInterface"
                type="Standard\Skudo\Model\EnvironmentProbe"/>
</config>
```

```php
<?php
// Api/EnvironmentProbeInterface.php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface EnvironmentProbeInterface
{
    /**
     * Describe el entorno Magento para que el ingestor se adapte a él.
     *
     * @return mixed[]
     */
    public function getProfile(): array;
}
```

- [ ] **Step 3: Escribir el test PHPUnit que falla**

```php
<?php
// Test/Unit/Model/EnvironmentProbeTest.php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\DB\Adapter\AdapterInterface;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Api\Data\StoreInterface;
use Magento\Store\Model\StoreManagerInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\EnvironmentProbe;

class EnvironmentProbeTest extends TestCase
{
    private function probe(bool $hasStaging, bool $hasRowId, bool $hasMsi): EnvironmentProbe
    {
        $metadata = $this->createMock(ProductMetadataInterface::class);
        $metadata->method('getEdition')->willReturn($hasStaging ? 'Enterprise' : 'Community');
        $metadata->method('getVersion')->willReturn('2.4.7-p3');

        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->willReturnCallback(
            static fn (string $name): bool => match ($name) {
                'Magento_Staging' => $hasStaging,
                'Magento_InventoryApi' => $hasMsi,
                default => false,
            }
        );

        $connection = $this->createMock(AdapterInterface::class);
        $connection->method('getTableName')->willReturnArgument(0);
        $connection->method('tableColumnExists')
            ->with('catalog_product_entity', 'row_id')
            ->willReturn($hasRowId);

        $resource = $this->createMock(ResourceConnection::class);
        $resource->method('getConnection')->willReturn($connection);
        $resource->method('getTableName')->willReturnArgument(0);

        $storeManager = $this->createMock(StoreManagerInterface::class);
        $storeManager->method('getWebsites')->willReturn([]);
        $storeManager->method('getGroups')->willReturn([]);
        $storeManager->method('getStores')->willReturn([]);

        return new EnvironmentProbe($metadata, $modules, $resource, $storeManager);
    }

    public function testOpenSourceReportsEntityId(): void
    {
        $profile = $this->probe(hasStaging: false, hasRowId: false, hasMsi: true)->getProfile();

        $this->assertSame('Community', $profile['edition']);
        $this->assertSame('entity_id', $profile['product_entity_key']);
        $this->assertFalse($profile['staging_enabled']);
        $this->assertTrue($profile['msi_enabled']);
    }

    public function testCommerceWithStagingReportsRowId(): void
    {
        $profile = $this->probe(hasStaging: true, hasRowId: true, hasMsi: true)->getProfile();

        $this->assertSame('row_id', $profile['product_entity_key']);
        $this->assertTrue($profile['staging_enabled']);
    }

    public function testKeyIsDetectedFromTheSchemaNotFromTheEdition(): void
    {
        // Enterprise sin el módulo Staging instalado sigue usando entity_id.
        $profile = $this->probe(hasStaging: false, hasRowId: false, hasMsi: false)->getProfile();

        $this->assertSame('entity_id', $profile['product_entity_key']);
        $this->assertFalse($profile['msi_enabled']);
    }
}
```

- [ ] **Step 4: Ejecutar y comprobar que falla**

Run (desde la raíz de una instalación Magento con el módulo enlazado):
`vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: FAIL con `Class "Standard\Skudo\Model\EnvironmentProbe" not found`

- [ ] **Step 5: Implementar `Model/EnvironmentProbe.php`**

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ProductMetadataInterface;
use Magento\Framework\App\ResourceConnection;
use Magento\Framework\Module\ModuleListInterface;
use Magento\Store\Model\StoreManagerInterface;
use Standard\Skudo\Api\EnvironmentProbeInterface;

class EnvironmentProbe implements EnvironmentProbeInterface
{
    private const MODULE_VERSION = '1.0.0';

    public function __construct(
        private readonly ProductMetadataInterface $metadata,
        private readonly ModuleListInterface $modules,
        private readonly ResourceConnection $resource,
        private readonly StoreManagerInterface $storeManager,
    ) {
    }

    public function getProfile(): array
    {
        $hasStaging = $this->modules->has('Magento_Staging');

        return [
            'edition' => $this->metadata->getEdition(),
            'version' => $this->metadata->getVersion(),
            // La clave se detecta del esquema, no de la edición: Commerce sin
            // Staging instalado sigue usando entity_id.
            'product_entity_key' => $this->detectProductEntityKey(),
            'staging_enabled' => $hasStaging,
            'msi_enabled' => $this->modules->has('Magento_InventoryApi'),
            'default_stock_id' => $this->modules->has('Magento_InventoryApi') ? 1 : null,
            'websites' => $this->websites(),
            'store_groups' => $this->storeGroups(),
            'store_views' => $this->storeViews(),
            'counts' => $this->counts(),
            'module_version' => self::MODULE_VERSION,
        ];
    }

    private function detectProductEntityKey(): string
    {
        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName('catalog_product_entity');

        return $connection->tableColumnExists($table, 'row_id') ? 'row_id' : 'entity_id';
    }

    private function websites(): array
    {
        $out = [];
        foreach ($this->storeManager->getWebsites() as $website) {
            $out[] = [
                'id' => (int) $website->getId(),
                'code' => (string) $website->getCode(),
                'name' => (string) $website->getName(),
            ];
        }
        return $out;
    }

    private function storeGroups(): array
    {
        $out = [];
        foreach ($this->storeManager->getGroups() as $group) {
            $out[] = [
                'id' => (int) $group->getId(),
                'website_id' => (int) $group->getWebsiteId(),
                'code' => (string) $group->getCode(),
                'name' => (string) $group->getName(),
                'root_category_id' => (int) $group->getRootCategoryId(),
            ];
        }
        return $out;
    }

    private function storeViews(): array
    {
        $out = [];
        foreach ($this->storeManager->getStores() as $store) {
            $out[] = [
                'id' => (int) $store->getId(),
                'group_id' => (int) $store->getStoreGroupId(),
                'code' => (string) $store->getCode(),
                'name' => (string) $store->getName(),
                'is_active' => (bool) $store->isActive(),
                'locale' => (string) $store->getConfig('general/locale/code'),
                'currency' => (string) $store->getCurrentCurrencyCode(),
            ];
        }
        return $out;
    }

    private function counts(): array
    {
        $connection = $this->resource->getConnection();
        $count = function (string $table) use ($connection): int {
            $name = $this->resource->getTableName($table);
            return (int) $connection->fetchOne(
                $connection->select()->from($name, 'COUNT(*)')
            );
        };

        return [
            'products' => $count('catalog_product_entity'),
            'attribute_sets' => $count('eav_attribute_set'),
            'attributes' => $count('eav_attribute'),
            'categories' => $count('catalog_category_entity'),
        ];
    }
}
```

- [ ] **Step 6: Ejecutar y comprobar que pasa**

Run: `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add magento-module/
git commit -m "feat(s0): módulo Standard_Skudo con sonda de entorno"
```

---

## Task 4: Esquema del espejo — tenants y topología de tiendas

**Files:**
- Create: `src/skudo/mirror/__init__.py`, `src/skudo/mirror/models.py`
- Create: `src/skudo/mirror/topology.py`
- Create: `alembic/versions/0001_tenants_and_topology.py`
- Test: `tests/mirror/test_topology.py`

**Interfaces:**
- Consumes: `EnvironmentProfile` (Task 2), fixture `db_session` (Task 1)
- Produces:
  - `skudo.mirror.models.Base` — `DeclarativeBase` que Alembic importa
  - modelos `Tenant`, `Website`, `StoreGroup`, `StoreView`, `EnvironmentSnapshot`
  - `sync_topology(session, tenant_id: int, profile: EnvironmentProfile) -> None`
  - `root_category_id(session, tenant_id: int, store_view_id: int) -> int`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/mirror/test_topology.py
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from skudo.magento.environment import parse_environment
from skudo.mirror.models import StoreView, Tenant
from skudo.mirror.topology import root_category_id, sync_topology

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def profile():
    return parse_environment(json.loads((FIXTURES / "environment_opensource.json").read_text()))


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://example.test",
                 token_env_var="SKUDO_TENANT_NISSEI_TOKEN")
    db_session.add(row)
    db_session.flush()
    return row


def test_sync_stores_the_two_store_views(db_session, tenant, profile):
    sync_topology(db_session, tenant.id, profile)

    codes = db_session.scalars(
        select(StoreView.code).where(StoreView.tenant_id == tenant.id).order_by(StoreView.code)
    ).all()
    assert codes == ["br_pt", "py_es"]


def test_root_category_is_resolved_per_store_view(db_session, tenant, profile):
    sync_topology(db_session, tenant.id, profile)

    assert root_category_id(db_session, tenant.id, 1) == 2
    assert root_category_id(db_session, tenant.id, 2) == 3


def test_sync_is_idempotent(db_session, tenant, profile):
    sync_topology(db_session, tenant.id, profile)
    sync_topology(db_session, tenant.id, profile)

    count = len(db_session.scalars(
        select(StoreView).where(StoreView.tenant_id == tenant.id)
    ).all())
    assert count == 2


def test_topology_is_isolated_per_tenant(db_session, profile):
    a = Tenant(code="a", name="A", base_url="https://a.test", token_env_var="X")
    b = Tenant(code="b", name="B", base_url="https://b.test", token_env_var="Y")
    db_session.add_all([a, b])
    db_session.flush()

    sync_topology(db_session, a.id, profile)

    assert db_session.scalars(
        select(StoreView).where(StoreView.tenant_id == b.id)
    ).all() == []
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `make up && uv run pytest tests/mirror/test_topology.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.mirror'`

- [ ] **Step 3: Implementar `src/skudo/mirror/models.py`**

```python
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(512))
    # Solo el NOMBRE de la variable de entorno. El token nunca vive en la base.
    token_env_var: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())


class EnvironmentSnapshot(Base):
    """Historial de lo que la sonda encontró. Sirve para detectar que el
    Magento del cliente cambió de versión o activó Staging bajo nuestros pies."""

    __tablename__ = "environment_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    edition: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    product_entity_key: Mapped[str] = mapped_column(String(16))
    staging_enabled: Mapped[bool] = mapped_column(Boolean)
    msi_enabled: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())


class Website(Base):
    __tablename__ = "website"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))


class StoreGroup(Base):
    __tablename__ = "store_group"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    website_magento_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    root_category_id: Mapped[int] = mapped_column(Integer)


class StoreView(Base):
    __tablename__ = "store_view"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    group_magento_id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean)
    locale: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8))
```

- [ ] **Step 4: Implementar `src/skudo/mirror/topology.py`**

```python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.magento.environment import EnvironmentProfile
from skudo.mirror.models import EnvironmentSnapshot, StoreGroup, StoreView, Website


def sync_topology(session: Session, tenant_id: int, profile: EnvironmentProfile) -> None:
    """Refleja websites, grupos y store views. Idempotente por (tenant, magento_id)."""
    session.add(
        EnvironmentSnapshot(
            tenant_id=tenant_id,
            edition=profile.edition,
            version=profile.version,
            product_entity_key=profile.product_entity_key,
            staging_enabled=profile.staging_enabled,
            msi_enabled=profile.msi_enabled,
            payload=profile.model_dump(),
        )
    )

    _upsert(session, Website, ["code", "name"], [
        {"tenant_id": tenant_id, "magento_id": w.id, "code": w.code, "name": w.name}
        for w in profile.websites
    ])
    _upsert(session, StoreGroup, ["website_magento_id", "code", "name", "root_category_id"], [
        {"tenant_id": tenant_id, "magento_id": g.id, "website_magento_id": g.website_id,
         "code": g.code, "name": g.name, "root_category_id": g.root_category_id}
        for g in profile.store_groups
    ])
    _upsert(session, StoreView,
            ["group_magento_id", "code", "name", "is_active", "locale", "currency"], [
        {"tenant_id": tenant_id, "magento_id": v.id, "group_magento_id": v.group_id,
         "code": v.code, "name": v.name, "is_active": v.is_active,
         "locale": v.locale, "currency": v.currency}
        for v in profile.store_views
    ])
    session.flush()


def _upsert(session: Session, model, update_columns: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    stmt = insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "magento_id"],
        set_={col: getattr(stmt.excluded, col) for col in update_columns},
    )
    session.execute(stmt)


def root_category_id(session: Session, tenant_id: int, store_view_id: int) -> int:
    """La store view no conoce su root category: la hereda de su grupo."""
    group_magento_id = session.scalar(
        select(StoreView.group_magento_id).where(
            StoreView.tenant_id == tenant_id, StoreView.magento_id == store_view_id
        )
    )
    if group_magento_id is None:
        raise KeyError(f"store view desconocida: {store_view_id}")

    root = session.scalar(
        select(StoreGroup.root_category_id).where(
            StoreGroup.tenant_id == tenant_id, StoreGroup.magento_id == group_magento_id
        )
    )
    if root is None:
        raise KeyError(f"store group desconocido: {group_magento_id}")
    return root
```

- [ ] **Step 5: Generar la migración**

Run: `make up && SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo uv run alembic revision --autogenerate -m "tenants and topology"`

Renombrar el archivo generado a `alembic/versions/0001_tenants_and_topology.py` y
revisar que crea las cinco tablas con sus `UniqueConstraint`.

- [ ] **Step 6: Ejecutar los tests y comprobar que pasan**

Run: `make test` (o `uv run pytest tests/mirror/test_topology.py -v`)
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add src/skudo/mirror/ alembic/versions/ tests/mirror/
git commit -m "feat(s0): espejo de tenants y topología de tiendas con root category derivada"
```

---

## Task 5: Esquema del espejo — atributos y opciones con identidad propia

Esta tarea implementa uno de los cuatro criterios de aceptación de S0: **una opción
traducida PY/BR se reconoce como la misma `option_id`**.

**Files:**
- Modify: `src/skudo/mirror/models.py` (añadir cuatro modelos al final)
- Create: `src/skudo/mirror/attributes.py`
- Create: `alembic/versions/0002_attributes_and_options.py`
- Test: `tests/mirror/test_attributes.py`

**Interfaces:**
- Consumes: `Base`, `Tenant` (Task 4)
- Produces:
  - modelos `AttributeSet`, `Attribute`, `AttributeOption`, `AttributeOptionLabel`
  - `upsert_attribute(session, tenant_id, payload: dict) -> None`
  - `upsert_option(session, tenant_id, attribute_code: str, option_id: int, labels: dict[int, str]) -> None`
  - `option_labels(session, tenant_id, attribute_code, option_id) -> dict[int, str]`
  - `distinct_option_ids(session, tenant_id, attribute_code) -> list[int]`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/mirror/test_attributes.py
import pytest

from skudo.mirror.attributes import (
    distinct_option_ids, option_labels, upsert_attribute, upsert_option
)
from skudo.mirror.models import Tenant


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://example.test",
                 token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def color_attribute(db_session, tenant):
    upsert_attribute(db_session, tenant.id, {
        "code": "color", "label": "Color", "frontend_input": "select",
        "declared_scope": "store", "is_filterable": True, "is_required": False,
        "attribute_set_ids": [4],
    })
    return "color"


def test_one_option_with_two_translations_is_one_option(db_session, tenant, color_attribute):
    """Negro (store 1) y Preto (store 2) son la MISMA opción.

    Este es el test que impide la corrección destructiva más peligrosa del
    sistema: consolidar dos etiquetas que en realidad son una traducción.
    """
    upsert_option(db_session, tenant.id, "color", option_id=17,
                  labels={0: "Negro", 1: "Negro", 2: "Preto"})

    assert distinct_option_ids(db_session, tenant.id, "color") == [17]
    assert option_labels(db_session, tenant.id, "color", 17) == {
        0: "Negro", 1: "Negro", 2: "Preto"
    }


def test_two_real_options_stay_separate(db_session, tenant, color_attribute):
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro", 2: "Preto"})
    upsert_option(db_session, tenant.id, "color", 18, {0: "Blanco", 2: "Branco"})

    assert distinct_option_ids(db_session, tenant.id, "color") == [17, 18]


def test_same_label_in_two_options_is_not_merged(db_session, tenant, color_attribute):
    """Dos opciones distintas pueden compartir etiqueta por error de datos.
    Siguen siendo dos opciones: la identidad es el option_id."""
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro"})
    upsert_option(db_session, tenant.id, "color", 99, {0: "Negro"})

    assert distinct_option_ids(db_session, tenant.id, "color") == [17, 99]


def test_relabeling_an_option_updates_in_place(db_session, tenant, color_attribute):
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro", 2: "Preto"})
    upsert_option(db_session, tenant.id, "color", 17, {0: "Negro mate", 2: "Preto mate"})

    assert option_labels(db_session, tenant.id, "color", 17) == {
        0: "Negro mate", 2: "Preto mate"
    }


def test_filterable_flag_is_stored_on_the_attribute(db_session, tenant, color_attribute):
    from sqlalchemy import select
    from skudo.mirror.models import Attribute

    row = db_session.scalar(
        select(Attribute).where(Attribute.tenant_id == tenant.id, Attribute.code == "color")
    )
    # is_filterable es propiedad del ATRIBUTO en Magento, no de la categoría.
    assert row.is_filterable is True
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/mirror/test_attributes.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.mirror.attributes'`

- [ ] **Step 3: Añadir los modelos al final de `src/skudo/mirror/models.py`**

```python
class AttributeSet(Base):
    __tablename__ = "attribute_set"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))


class Attribute(Base):
    __tablename__ = "attribute"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    code: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(255))
    frontend_input: Mapped[str] = mapped_column(String(64))
    # 'global' | 'website' | 'store', tal como Magento lo declara.
    declared_scope: Mapped[str] = mapped_column(String(16))
    is_filterable: Mapped[bool] = mapped_column(Boolean)
    is_required: Mapped[bool] = mapped_column(Boolean)
    attribute_set_ids: Mapped[list] = mapped_column(JSON)


class AttributeOption(Base):
    """La identidad de una opción es su option_id de Magento, jamás su etiqueta."""

    __tablename__ = "attribute_option"
    __table_args__ = (UniqueConstraint("tenant_id", "attribute_code", "magento_option_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    attribute_code: Mapped[str] = mapped_column(String(255), index=True)
    magento_option_id: Mapped[int] = mapped_column(Integer)


class AttributeOptionLabel(Base):
    __tablename__ = "attribute_option_label"
    __table_args__ = (UniqueConstraint("option_row_id", "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    option_row_id: Mapped[int] = mapped_column(ForeignKey("attribute_option.id"), index=True)
    # 0 = etiqueta por defecto (admin), N = etiqueta de esa store view.
    store_view_magento_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(512))
```

- [ ] **Step 4: Implementar `src/skudo/mirror/attributes.py`**

```python
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute, AttributeOption, AttributeOptionLabel


def upsert_attribute(session: Session, tenant_id: int, payload: dict) -> None:
    stmt = insert(Attribute).values(
        tenant_id=tenant_id,
        code=payload["code"],
        label=payload["label"],
        frontend_input=payload["frontend_input"],
        declared_scope=payload["declared_scope"],
        is_filterable=payload["is_filterable"],
        is_required=payload["is_required"],
        attribute_set_ids=payload["attribute_set_ids"],
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "code"],
            set_={
                "label": stmt.excluded.label,
                "frontend_input": stmt.excluded.frontend_input,
                "declared_scope": stmt.excluded.declared_scope,
                "is_filterable": stmt.excluded.is_filterable,
                "is_required": stmt.excluded.is_required,
                "attribute_set_ids": stmt.excluded.attribute_set_ids,
            },
        )
    )
    session.flush()


def upsert_option(
    session: Session,
    tenant_id: int,
    attribute_code: str,
    option_id: int,
    labels: dict[int, str],
) -> None:
    """Guarda una opción y todas sus etiquetas por store view.

    `labels` va indexado por store view de Magento; 0 es la etiqueta admin.
    Las etiquetas se reemplazan por completo: son un reflejo, no un histórico.
    """
    stmt = insert(AttributeOption).values(
        tenant_id=tenant_id, attribute_code=attribute_code, magento_option_id=option_id
    )
    session.execute(
        stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "attribute_code", "magento_option_id"]
        )
    )
    session.flush()

    row_id = session.scalar(
        select(AttributeOption.id).where(
            AttributeOption.tenant_id == tenant_id,
            AttributeOption.attribute_code == attribute_code,
            AttributeOption.magento_option_id == option_id,
        )
    )

    session.execute(
        delete(AttributeOptionLabel).where(AttributeOptionLabel.option_row_id == row_id)
    )
    if labels:
        session.execute(
            insert(AttributeOptionLabel).values([
                {"option_row_id": row_id, "store_view_magento_id": store_id, "label": label}
                for store_id, label in labels.items()
            ])
        )
    session.flush()


def option_labels(
    session: Session, tenant_id: int, attribute_code: str, option_id: int
) -> dict[int, str]:
    rows = session.execute(
        select(AttributeOptionLabel.store_view_magento_id, AttributeOptionLabel.label)
        .join(AttributeOption, AttributeOption.id == AttributeOptionLabel.option_row_id)
        .where(
            AttributeOption.tenant_id == tenant_id,
            AttributeOption.attribute_code == attribute_code,
            AttributeOption.magento_option_id == option_id,
        )
    ).all()
    return {store_id: label for store_id, label in rows}


def distinct_option_ids(session: Session, tenant_id: int, attribute_code: str) -> list[int]:
    return list(
        session.scalars(
            select(AttributeOption.magento_option_id)
            .where(
                AttributeOption.tenant_id == tenant_id,
                AttributeOption.attribute_code == attribute_code,
            )
            .order_by(AttributeOption.magento_option_id)
        ).all()
    )
```

- [ ] **Step 5: Generar la migración**

Run: `SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo uv run alembic revision --autogenerate -m "attributes and options"`

Renombrar a `alembic/versions/0002_attributes_and_options.py`.

- [ ] **Step 6: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/mirror/test_attributes.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add src/skudo/mirror/ alembic/versions/ tests/mirror/
git commit -m "feat(s0): opciones de atributo con identidad por option_id y labels por store view"
```

---

## Task 6: Esquema del espejo — registros de producto con procedencia de scope

**Files:**
- Modify: `src/skudo/mirror/models.py` (añadir `ProductRecord` al final)
- Create: `src/skudo/mirror/products.py`
- Create: `alembic/versions/0003_product_records.py`
- Test: `tests/mirror/test_products.py`

**Interfaces:**
- Consumes: `Base`, `Tenant`, `StoreView` (Task 4)
- Produces:
  - modelo `ProductRecord`
  - `ProductIdentity` (pydantic): `sku: str`, `mpn: str | None`, `model: str | None`,
    `gtin: str | None`, `variant_key: str | None`
  - `resolve_scope(global_values: dict, store_values: dict) -> tuple[dict, dict]`
    devolviendo `(effective, provenance)` donde `provenance[code] in {"global","store"}`
  - `upsert_record(session, tenant_id, store_view_magento_id, identity, effective, provenance, magento_updated_at) -> None`
  - `get_record(session, tenant_id, sku, store_view_magento_id) -> ProductRecord | None`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/mirror/test_products.py
from datetime import UTC, datetime

import pytest

from skudo.mirror.models import Tenant
from skudo.mirror.products import (
    ProductIdentity, get_record, resolve_scope, upsert_record
)


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_store_value_wins_and_is_marked_as_store():
    effective, provenance = resolve_scope(
        global_values={"name": "Notebook", "color": "17"},
        store_values={"name": "Notebook BR"},
    )
    assert effective == {"name": "Notebook BR", "color": "17"}
    assert provenance == {"name": "store", "color": "global"}


def test_absent_store_value_falls_back_to_global():
    effective, provenance = resolve_scope({"weight": "2.1"}, {})
    assert effective == {"weight": "2.1"}
    assert provenance == {"weight": "global"}


def test_empty_string_at_store_scope_is_still_a_store_value():
    """Un valor vacío puesto a propósito en la store view NO es herencia.
    Colapsarlo con 'global' ocultaría un defecto real de traducción."""
    effective, provenance = resolve_scope({"name": "Notebook"}, {"name": ""})
    assert effective == {"name": ""}
    assert provenance == {"name": "store"}


def test_identity_preserves_leading_zeros_and_suffixes(db_session, tenant):
    identity = ProductIdentity(sku="0074", mpn="ABC-123/B", model="X1", gtin="07501234567890")
    upsert_record(db_session, tenant.id, 1, identity, {"name": "N"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.sku == "0074"
    assert row.mpn == "ABC-123/B"
    assert row.gtin == "07501234567890"


def test_the_same_product_has_one_record_per_store_view(db_session, tenant):
    identity = ProductIdentity(sku="SKU1", mpn=None, model=None, gtin=None)
    for store_id, name in ((1, "Aire Acondicionado"), (2, "Ar Condicionado")):
        upsert_record(db_session, tenant.id, store_id, identity, {"name": name},
                      {"name": "store"}, datetime(2026, 9, 1, tzinfo=UTC))

    assert get_record(db_session, tenant.id, "SKU1", 1).attributes["name"] == "Aire Acondicionado"
    assert get_record(db_session, tenant.id, "SKU1", 2).attributes["name"] == "Ar Condicionado"


def test_upsert_replaces_and_updates_the_content_hash(db_session, tenant):
    identity = ProductIdentity(sku="SKU1", mpn=None, model=None, gtin=None)
    upsert_record(db_session, tenant.id, 1, identity, {"name": "A"}, {"name": "global"},
                  datetime(2026, 9, 1, tzinfo=UTC))
    first = get_record(db_session, tenant.id, "SKU1", 1).content_hash

    upsert_record(db_session, tenant.id, 1, identity, {"name": "B"}, {"name": "global"},
                  datetime(2026, 9, 2, tzinfo=UTC))
    row = get_record(db_session, tenant.id, "SKU1", 1)

    assert row.attributes["name"] == "B"
    assert row.content_hash != first
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/mirror/test_products.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.mirror.products'`

- [ ] **Step 3: Añadir el modelo al final de `src/skudo/mirror/models.py`**

```python
class ProductRecord(Base):
    """El objeto central del sistema: (tenant, producto, store view).

    Nunca (tenant, producto). Un producto tiene un registro por store view
    porque su calidad puede ser distinta en PY y en BR.
    """

    __tablename__ = "product_record"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)

    # Identidad desglosada, toda como texto: 0074 != 74, ABC-123/B != ABC-123.
    sku: Mapped[str] = mapped_column(String(255), index=True)
    mpn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    variant_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attributes: Mapped[dict] = mapped_column(JSON)
    # {codigo_atributo: "global"|"store"} — de dónde salió cada valor efectivo.
    scope_provenance: Mapped[dict] = mapped_column(JSON)

    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    magento_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mirrored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now())
```

- [ ] **Step 4: Implementar `src/skudo/mirror/products.py`**

```python
import hashlib
import json
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductRecord


class ProductIdentity(BaseModel):
    """Identidad desglosada. Todo texto: normalizar a número destruye la identidad."""

    sku: str
    mpn: str | None = None
    model: str | None = None
    gtin: str | None = None
    variant_key: str | None = None


def resolve_scope(
    global_values: dict[str, str], store_values: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Resuelve el valor efectivo y registra de dónde vino.

    La presencia de una clave en `store_values` decide la procedencia, no su
    contenido: un valor vacío puesto en la store view es un valor de store view,
    y colapsarlo con la herencia global ocultaría un defecto de traducción.
    """
    effective: dict[str, str] = dict(global_values)
    provenance: dict[str, str] = {code: "global" for code in global_values}

    for code, value in store_values.items():
        effective[code] = value
        provenance[code] = "store"

    return effective, provenance


def content_hash(identity: ProductIdentity, effective: dict) -> str:
    """Hash estable del contenido relevante. Base del caché de la capa IA en S6."""
    material = json.dumps(
        {"identity": identity.model_dump(), "attributes": effective},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def upsert_record(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    identity: ProductIdentity,
    effective: dict,
    provenance: dict,
    magento_updated_at: datetime,
) -> None:
    stmt = insert(ProductRecord).values(
        tenant_id=tenant_id,
        store_view_magento_id=store_view_magento_id,
        sku=identity.sku,
        mpn=identity.mpn,
        model=identity.model,
        gtin=identity.gtin,
        variant_key=identity.variant_key,
        attributes=effective,
        scope_provenance=provenance,
        content_hash=content_hash(identity, effective),
        magento_updated_at=magento_updated_at,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku", "store_view_magento_id"],
            set_={
                "mpn": stmt.excluded.mpn,
                "model": stmt.excluded.model,
                "gtin": stmt.excluded.gtin,
                "variant_key": stmt.excluded.variant_key,
                "attributes": stmt.excluded.attributes,
                "scope_provenance": stmt.excluded.scope_provenance,
                "content_hash": stmt.excluded.content_hash,
                "magento_updated_at": stmt.excluded.magento_updated_at,
            },
        )
    )
    session.flush()


def get_record(
    session: Session, tenant_id: int, sku: str, store_view_magento_id: int
) -> ProductRecord | None:
    return session.scalar(
        select(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.sku == sku,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
```

- [ ] **Step 5: Generar la migración**

Run: `SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo uv run alembic revision --autogenerate -m "product records"`

Renombrar a `alembic/versions/0003_product_records.py`.

- [ ] **Step 6: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/mirror/test_products.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add src/skudo/mirror/ alembic/versions/ tests/mirror/
git commit -m "feat(s0): registros de producto por store view con procedencia de scope"
```

---

## Task 7: Categorías — asignación global y efecto derivado por store view

Implementa el criterio de aceptación más sutil de S0. En el core de Magento
`catalog_category_product` **no tiene `store_id`**: la asignación es global. Que un
producto sea alcanzable en BR depende de la root category del grupo, del `is_active` de
la categoría en esa tienda y de los websites del producto. Confundir asignación con
efecto fue un error de la primera versión del diseño.

**Files:**
- Modify: `src/skudo/mirror/models.py` (añadir tres modelos al final)
- Create: `src/skudo/mirror/categories.py`
- Create: `alembic/versions/0004_categories.py`
- Test: `tests/mirror/test_categories.py`

**Interfaces:**
- Consumes: `Base`, `Tenant` (Task 4); `root_category_id` (Task 4)
- Produces:
  - modelos `Category`, `CategoryStoreState`, `ProductCategoryAssignment`
  - `CategoryEffect` (pydantic): `category_magento_id: int`, `store_view_magento_id: int`,
    `is_effective: bool`, `reason: str`
  - `derive_category_effect(assignment_path: list[int], root_category_id: int, is_active_in_store: bool, product_website_ids: list[int], store_website_id: int) -> CategoryEffect`
  - `upsert_category(session, tenant_id, magento_id, path: list[int], name: str) -> None`
  - `set_category_store_state(session, tenant_id, magento_id, store_view_magento_id, is_active: bool, name: str) -> None`
  - `assign_product(session, tenant_id, sku: str, category_magento_id: int) -> None`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/mirror/test_categories.py
import pytest
from sqlalchemy import select

from skudo.mirror.categories import (
    assign_product, derive_category_effect, set_category_store_state, upsert_category
)
from skudo.mirror.models import ProductCategoryAssignment, Tenant

# Escenario: categoría 15 cuelga del árbol 2 (root de PY). El árbol de BR es 3.
PATH_UNDER_PY_ROOT = [1, 2, 15]


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_effective_when_all_four_conditions_hold():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=2,
        is_active_in_store=True, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is True


def test_not_effective_when_category_is_outside_the_store_root():
    """La asignación existe, pero la categoría no cuelga del árbol de esa tienda."""
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=3,
        is_active_in_store=True, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "fuera_del_arbol_de_la_tienda"


def test_not_effective_when_category_is_inactive_in_that_store():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=2,
        is_active_in_store=False, product_website_ids=[1], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "categoria_inactiva_en_la_tienda"


def test_not_effective_when_product_is_not_in_the_store_website():
    effect = derive_category_effect(
        assignment_path=PATH_UNDER_PY_ROOT, root_category_id=2,
        is_active_in_store=True, product_website_ids=[2], store_website_id=1,
    )
    assert effect.is_effective is False
    assert effect.reason == "producto_fuera_del_website"


def test_assignment_is_stored_without_store_scope(db_session, tenant):
    """La tabla de asignación NO lleva store view: en Magento es global."""
    upsert_category(db_session, tenant.id, 15, PATH_UNDER_PY_ROOT, "Climatización")
    assign_product(db_session, tenant.id, "SKU1", 15)

    rows = db_session.scalars(
        select(ProductCategoryAssignment).where(
            ProductCategoryAssignment.tenant_id == tenant.id
        )
    ).all()
    assert len(rows) == 1
    assert not hasattr(rows[0], "store_view_magento_id")


def test_category_name_and_activity_are_per_store_view(db_session, tenant):
    upsert_category(db_session, tenant.id, 15, PATH_UNDER_PY_ROOT, "Climatización")
    set_category_store_state(db_session, tenant.id, 15, 1, True, "Climatización")
    set_category_store_state(db_session, tenant.id, 15, 2, False, "Climatização")

    from skudo.mirror.models import CategoryStoreState

    states = {
        s.store_view_magento_id: (s.is_active, s.name)
        for s in db_session.scalars(
            select(CategoryStoreState).where(CategoryStoreState.tenant_id == tenant.id)
        ).all()
    }
    assert states == {1: (True, "Climatización"), 2: (False, "Climatização")}
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/mirror/test_categories.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.mirror.categories'`

- [ ] **Step 3: Añadir los modelos al final de `src/skudo/mirror/models.py`**

```python
class Category(Base):
    __tablename__ = "category"
    __table_args__ = (UniqueConstraint("tenant_id", "magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    magento_id: Mapped[int] = mapped_column(Integer)
    # Ruta completa de ids desde la raíz. Permite decidir el árbol sin recursión.
    path: Mapped[list] = mapped_column(JSON)
    default_name: Mapped[str] = mapped_column(String(512))


class CategoryStoreState(Base):
    """Nombre y actividad de la categoría EN una store view. Esto sí tiene scope."""

    __tablename__ = "category_store_state"
    __table_args__ = (UniqueConstraint("tenant_id", "category_magento_id",
                                       "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    category_magento_id: Mapped[int] = mapped_column(Integer, index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    name: Mapped[str] = mapped_column(String(512))


class ProductCategoryAssignment(Base):
    """Asignación producto-categoría: GLOBAL, sin store view.

    En el core de Magento `catalog_category_product` no tiene store_id. El efecto
    por tienda se deriva (ver derive_category_effect), nunca se guarda aquí.
    """

    __tablename__ = "product_category_assignment"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", "category_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    sku: Mapped[str] = mapped_column(String(255), index=True)
    category_magento_id: Mapped[int] = mapped_column(Integer, index=True)
```

- [ ] **Step 4: Implementar `src/skudo/mirror/categories.py`**

```python
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import Category, CategoryStoreState, ProductCategoryAssignment


class CategoryEffect(BaseModel):
    category_magento_id: int = 0
    store_view_magento_id: int = 0
    is_effective: bool
    reason: str


def derive_category_effect(
    assignment_path: list[int],
    root_category_id: int,
    is_active_in_store: bool,
    product_website_ids: list[int],
    store_website_id: int,
) -> CategoryEffect:
    """Decide si una asignación global tiene efecto en una store view concreta.

    Tres condiciones independientes, evaluadas en el orden en que un merchandiser
    las diagnosticaría. Se devuelve la primera que falla como `reason`, para que la
    UI pueda decir POR QUÉ el producto no aparece en vez de solo que no aparece.
    """
    if root_category_id not in assignment_path:
        return CategoryEffect(is_effective=False, reason="fuera_del_arbol_de_la_tienda")
    if not is_active_in_store:
        return CategoryEffect(is_effective=False, reason="categoria_inactiva_en_la_tienda")
    if store_website_id not in product_website_ids:
        return CategoryEffect(is_effective=False, reason="producto_fuera_del_website")
    return CategoryEffect(is_effective=True, reason="efectiva")


def upsert_category(
    session: Session, tenant_id: int, magento_id: int, path: list[int], name: str
) -> None:
    stmt = insert(Category).values(
        tenant_id=tenant_id, magento_id=magento_id, path=path, default_name=name
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "magento_id"],
            set_={"path": stmt.excluded.path, "default_name": stmt.excluded.default_name},
        )
    )
    session.flush()


def set_category_store_state(
    session: Session,
    tenant_id: int,
    magento_id: int,
    store_view_magento_id: int,
    is_active: bool,
    name: str,
) -> None:
    stmt = insert(CategoryStoreState).values(
        tenant_id=tenant_id, category_magento_id=magento_id,
        store_view_magento_id=store_view_magento_id, is_active=is_active, name=name,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "category_magento_id", "store_view_magento_id"],
            set_={"is_active": stmt.excluded.is_active, "name": stmt.excluded.name},
        )
    )
    session.flush()


def assign_product(
    session: Session, tenant_id: int, sku: str, category_magento_id: int
) -> None:
    stmt = insert(ProductCategoryAssignment).values(
        tenant_id=tenant_id, sku=sku, category_magento_id=category_magento_id
    )
    session.execute(
        stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "sku", "category_magento_id"]
        )
    )
    session.flush()
```

- [ ] **Step 5: Generar la migración**

Run: `SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo uv run alembic revision --autogenerate -m "categories"`

Renombrar a `alembic/versions/0004_categories.py`.

- [ ] **Step 6: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/mirror/test_categories.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add src/skudo/mirror/ alembic/versions/ tests/mirror/
git commit -m "feat(s0): asignación global de categorías con efecto derivado por store view"
```

---

## Task 8: Módulo Magento — lectura masiva por cursor

**Files:**
- Create: `magento-module/Standard/Skudo/Model/Cursor.php`
- Create: `magento-module/Standard/Skudo/Api/ProductReaderInterface.php`
- Create: `magento-module/Standard/Skudo/Model/ProductReader.php`
- Modify: `magento-module/Standard/Skudo/etc/webapi.xml` (añadir la ruta)
- Modify: `magento-module/Standard/Skudo/etc/di.xml` (añadir la preferencia)
- Test: `magento-module/Standard/Skudo/Test/Unit/Model/CursorTest.php`

**Interfaces:**
- Consumes: `EnvironmentProbe::detectProductEntityKey` (Task 3), conceptualmente
- Produces: `GET /rest/V1/skudo/products?storeId=&limit=&cursor=` devolviendo
  `{"items": [...], "next_cursor": "..."|null}`. Cada item:
  `{sku, mpn, model, gtin, variant_key, global_values, store_values, website_ids, category_ids, updated_at}`

- [ ] **Step 1: Escribir el test de cursor que falla**

```php
<?php
// Test/Unit/Model/CursorTest.php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\Cursor;

class CursorTest extends TestCase
{
    public function testRoundTrip(): void
    {
        $cursor = new Cursor();
        $this->assertSame(4821, $cursor->decode($cursor->encode(4821)));
    }

    public function testEmptyCursorMeansStartFromTheBeginning(): void
    {
        $this->assertSame(0, (new Cursor())->decode(null));
        $this->assertSame(0, (new Cursor())->decode(''));
    }

    public function testTamperedCursorIsRejected(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        (new Cursor())->decode('no-es-un-cursor');
    }

    public function testNegativeKeyIsRejected(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        (new Cursor())->encode(-1);
    }
}
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: FAIL con `Class "Standard\Skudo\Model\Cursor" not found`

- [ ] **Step 3: Implementar `Model/Cursor.php`**

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

/**
 * Cursor de paginación por keyset.
 *
 * Se pagina por la clave de entidad y NO por offset: en un catálogo de cientos
 * de miles de productos el offset degrada de forma cuadrática y además puede
 * saltarse o repetir filas cuando el catálogo cambia entre páginas.
 */
class Cursor
{
    private const PREFIX = 'skudo1:';

    public function encode(int $lastKey): string
    {
        if ($lastKey < 0) {
            throw new \InvalidArgumentException('la clave del cursor no puede ser negativa');
        }
        return base64_encode(self::PREFIX . $lastKey);
    }

    public function decode(?string $cursor): int
    {
        if ($cursor === null || $cursor === '') {
            return 0;
        }

        $decoded = base64_decode($cursor, true);
        if ($decoded === false || !str_starts_with($decoded, self::PREFIX)) {
            throw new \InvalidArgumentException('cursor inválido');
        }

        $value = substr($decoded, strlen(self::PREFIX));
        if (!ctype_digit($value)) {
            throw new \InvalidArgumentException('cursor inválido');
        }

        return (int) $value;
    }
}
```

- [ ] **Step 4: Ejecutar y comprobar que pasa**

Run: `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: 7 passed (3 de EnvironmentProbe + 4 de Cursor)

- [ ] **Step 5: Declarar el contrato del lector**

```php
<?php
// Api/ProductReaderInterface.php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface ProductReaderInterface
{
    /**
     * Lee una página de productos con sus valores global y de store view.
     *
     * @param int $storeId
     * @param int $limit
     * @param string|null $cursor
     * @return mixed[]
     */
    public function getPage(int $storeId, int $limit = 500, ?string $cursor = null): array;
}
```

- [ ] **Step 6: Implementar `Model/ProductReader.php`**

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Standard\Skudo\Api\ProductReaderInterface;

class ProductReader implements ProductReaderInterface
{
    private const MAX_LIMIT = 1000;

    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly Cursor $cursor,
        private readonly EnvironmentProbe $probe,
    ) {
    }

    public function getPage(int $storeId, int $limit = 500, ?string $cursor = null): array
    {
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $after = $this->cursor->decode($cursor);
        $keyColumn = $this->probe->getProfile()['product_entity_key'];

        $connection = $this->resource->getConnection();
        $entity = $this->resource->getTableName('catalog_product_entity');

        $select = $connection->select()
            ->from(['e' => $entity], [
                'key' => 'e.' . $keyColumn,
                'sku' => 'e.sku',
                'attribute_set_id' => 'e.attribute_set_id',
                'type_id' => 'e.type_id',
                'updated_at' => 'e.updated_at',
            ])
            ->where('e.' . $keyColumn . ' > ?', $after)
            ->order('e.' . $keyColumn . ' ASC')
            ->limit($limit);

        $rows = $connection->fetchAll($select);
        if ($rows === []) {
            return ['items' => [], 'next_cursor' => null];
        }

        $keys = array_column($rows, 'key');
        $globals = $this->eavValues($keyColumn, $keys, 0);
        $stores = $this->eavValues($keyColumn, $keys, $storeId);
        $websites = $this->websiteIds($keys, $keyColumn);
        $categories = $this->categoryIds(array_column($rows, 'sku'));

        $items = [];
        foreach ($rows as $row) {
            $key = (int) $row['key'];
            $globalValues = $globals[$key] ?? [];
            $items[] = [
                'sku' => (string) $row['sku'],
                // Identidad desglosada; todo texto, sin normalizar.
                'mpn' => $globalValues['mpn'] ?? null,
                'model' => $globalValues['model'] ?? null,
                'gtin' => $globalValues['gtin'] ?? null,
                'variant_key' => $globalValues['variant_key'] ?? null,
                'attribute_set_id' => (int) $row['attribute_set_id'],
                'type_id' => (string) $row['type_id'],
                'global_values' => $globalValues,
                'store_values' => $stores[$key] ?? [],
                'website_ids' => $websites[$key] ?? [],
                'category_ids' => $categories[(string) $row['sku']] ?? [],
                'updated_at' => (string) $row['updated_at'],
            ];
        }

        return [
            'items' => $items,
            'next_cursor' => count($rows) < $limit
                ? null
                : $this->cursor->encode((int) end($keys)),
        ];
    }

    /**
     * Valores EAV de un scope concreto, agrupados por clave de entidad.
     *
     * store_id = 0 es el valor global; store_id = N el override de esa store view.
     * La ausencia de fila en N es lo que significa "hereda"; por eso los dos
     * scopes se leen por separado y se resuelven en el ingestor, no aquí.
     *
     * @param int[] $keys
     * @return array<int, array<string, string>>
     */
    private function eavValues(string $keyColumn, array $keys, int $storeId): array
    {
        $connection = $this->resource->getConnection();
        $attribute = $this->resource->getTableName('eav_attribute');
        $out = [];

        foreach (['varchar', 'int', 'decimal', 'text', 'datetime'] as $type) {
            $table = $this->resource->getTableName('catalog_product_entity_' . $type);
            $select = $connection->select()
                ->from(['v' => $table], ['entity' => 'v.' . $keyColumn, 'value' => 'v.value'])
                ->join(['a' => $attribute], 'a.attribute_id = v.attribute_id', ['code' => 'a.attribute_code'])
                ->where('v.' . $keyColumn . ' IN (?)', $keys)
                ->where('v.store_id = ?', $storeId);

            foreach ($connection->fetchAll($select) as $row) {
                $out[(int) $row['entity']][(string) $row['code']] = $row['value'] === null
                    ? null
                    : (string) $row['value'];
            }
        }

        return $out;
    }

    /**
     * @param int[] $keys
     * @return array<int, int[]>
     */
    private function websiteIds(array $keys, string $keyColumn): array
    {
        $connection = $this->resource->getConnection();
        $table = $this->resource->getTableName('catalog_product_website');
        $entity = $this->resource->getTableName('catalog_product_entity');

        // catalog_product_website siempre referencia entity_id, incluso con Staging.
        $select = $connection->select()
            ->from(['w' => $table], ['website_id' => 'w.website_id'])
            ->join(['e' => $entity], 'e.entity_id = w.product_id', ['key' => 'e.' . $keyColumn])
            ->where('e.' . $keyColumn . ' IN (?)', $keys);

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(int) $row['key']][] = (int) $row['website_id'];
        }
        return $out;
    }

    /**
     * @param string[] $skus
     * @return array<string, int[]>
     */
    private function categoryIds(array $skus): array
    {
        $connection = $this->resource->getConnection();
        $link = $this->resource->getTableName('catalog_category_product');
        $entity = $this->resource->getTableName('catalog_product_entity');

        // Sin store_id: la asignación es global. El efecto por tienda lo deriva
        // el ingestor con derive_category_effect.
        $select = $connection->select()
            ->from(['l' => $link], ['category_id' => 'l.category_id'])
            ->join(['e' => $entity], 'e.entity_id = l.product_id', ['sku' => 'e.sku'])
            ->where('e.sku IN (?)', $skus);

        $out = [];
        foreach ($connection->fetchAll($select) as $row) {
            $out[(string) $row['sku']][] = (int) $row['category_id'];
        }
        return $out;
    }
}
```

- [ ] **Step 7: Declarar la ruta y la preferencia**

Añadir a `etc/webapi.xml` dentro de `<routes>`:

```xml
    <route url="/V1/skudo/products" method="GET">
        <service class="Standard\Skudo\Api\ProductReaderInterface" method="getPage"/>
        <resources><resource ref="Standard_Skudo::read"/></resources>
    </route>
```

Añadir a `etc/di.xml` dentro de `<config>`:

```xml
    <preference for="Standard\Skudo\Api\ProductReaderInterface"
                type="Standard\Skudo\Model\ProductReader"/>
```

- [ ] **Step 8: Verificar contra una instancia real**

Run: `curl -s -H "Authorization: Bearer $TOKEN" "$MAGENTO_URL/rest/V1/skudo/products?storeId=1&limit=2" | python3 -m json.tool`
Expected: dos items con `global_values` y `store_values` separados, y `next_cursor` no nulo.

- [ ] **Step 9: Commit**

```bash
git add magento-module/
git commit -m "feat(s0): lectura masiva por cursor con scopes global y store separados"
```

---

## Task 9: Cliente HTTP y carga completa

**Files:**
- Create: `src/skudo/magento/client.py`
- Create: `src/skudo/ingest/__init__.py`, `src/skudo/ingest/full_sync.py`
- Test: `tests/ingest/test_full_sync.py`

**Interfaces:**
- Consumes: `Settings` (Task 1); `parse_environment` (Task 2); `sync_topology` (Task 4);
  `upsert_record`, `resolve_scope`, `ProductIdentity` (Task 6); `upsert_category`,
  `assign_product` (Task 7)
- Produces:
  - `MagentoClient(base_url: str, token: str, transport=None)` con
    `environment() -> EnvironmentProfile` e
    `iter_products(store_id: int, limit: int = 500) -> Iterator[dict]`
  - `full_sync(session, client: MagentoClient, tenant_id: int, store_view_ids: list[int]) -> FullSyncReport`
  - `FullSyncReport` (pydantic): `records_written: int`, `pages_fetched: int`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/ingest/test_full_sync.py
import json
from pathlib import Path

import httpx
import pytest

from skudo.ingest.full_sync import full_sync
from skudo.magento.client import MagentoClient
from skudo.mirror.models import Tenant
from skudo.mirror.products import get_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

PAGE_1 = {
    "items": [
        {"sku": "0074", "mpn": "ABC-123/B", "model": "X1", "gtin": None,
         "variant_key": None, "attribute_set_id": 4, "type_id": "simple",
         "global_values": {"name": "Notebook", "weight": "2.1"},
         "store_values": {}, "website_ids": [1], "category_ids": [15],
         "updated_at": "2026-09-01 10:00:00"},
    ],
    "next_cursor": "c2t1ZG8xOjc0",
}
PAGE_2 = {
    "items": [
        {"sku": "SKU2", "mpn": None, "model": None, "gtin": None, "variant_key": None,
         "attribute_set_id": 4, "type_id": "simple",
         "global_values": {"name": "Aire Acondicionado"},
         "store_values": {"name": "Ar Condicionado"},
         "website_ids": [1], "category_ids": [], "updated_at": "2026-09-02 11:00:00"},
    ],
    "next_cursor": None,
}


def make_client() -> MagentoClient:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if request.url.path.endswith("/products"):
            cursor = request.url.params.get("cursor")
            return httpx.Response(200, json=PAGE_2 if cursor else PAGE_1)
        return httpx.Response(404)

    return MagentoClient(
        "https://x.test", "token", transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_full_sync_walks_every_page(db_session, tenant):
    report = full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    assert report.pages_fetched == 2
    assert report.records_written == 2


def test_global_value_is_mirrored_with_global_provenance(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.attributes["name"] == "Notebook"
    assert row.scope_provenance["name"] == "global"


def test_store_override_is_mirrored_with_store_provenance(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "SKU2", 1)
    assert row.attributes["name"] == "Ar Condicionado"
    assert row.scope_provenance["name"] == "store"


def test_identity_survives_the_round_trip(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    row = get_record(db_session, tenant.id, "0074", 1)
    assert row.sku == "0074"
    assert row.mpn == "ABC-123/B"


def test_full_sync_is_idempotent(db_session, tenant):
    full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])
    second = full_sync(db_session, make_client(), tenant.id, store_view_ids=[1])

    from sqlalchemy import func, select
    from skudo.mirror.models import ProductRecord

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == tenant.id
        )
    )
    assert second.records_written == 2
    assert total == 2
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/ingest/test_full_sync.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.magento.client'`

- [ ] **Step 3: Implementar `src/skudo/magento/client.py`**

```python
from collections.abc import Iterator

import httpx

from skudo.magento.environment import EnvironmentProfile, parse_environment


class MagentoClient:
    """Cliente del módulo Standard_Skudo. Solo lectura en S0."""

    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/rest/V1/skudo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=transport,
        )

    def environment(self) -> EnvironmentProfile:
        response = self._client.get("/environment")
        response.raise_for_status()
        return parse_environment(response.json())

    def iter_products(self, store_id: int, limit: int = 500) -> Iterator[dict]:
        """Recorre el catálogo página a página. Cada yield es una página completa."""
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"storeId": store_id, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            response = self._client.get("/products", params=params)
            response.raise_for_status()
            page = response.json()

            yield page

            cursor = page.get("next_cursor")
            if not cursor:
                return

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Implementar `src/skudo/ingest/full_sync.py`**

```python
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from skudo.magento.client import MagentoClient
from skudo.mirror.categories import assign_product
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record
from skudo.mirror.topology import sync_topology


class FullSyncReport(BaseModel):
    records_written: int = 0
    pages_fetched: int = 0


def _parse_magento_datetime(raw: str) -> datetime:
    """Magento devuelve 'YYYY-MM-DD HH:MM:SS' en UTC, sin zona explícita."""
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def full_sync(
    session: Session,
    client: MagentoClient,
    tenant_id: int,
    store_view_ids: list[int],
) -> FullSyncReport:
    """Carga completa del catálogo, una pasada por store view.

    Se recorre por store view porque los valores de override viven en ese scope:
    una sola pasada global no permitiría saber qué heredó cada tienda.
    """
    profile = client.environment()
    sync_topology(session, tenant_id, profile)

    report = FullSyncReport()

    for store_id in store_view_ids:
        for page in client.iter_products(store_id):
            report.pages_fetched += 1
            for item in page["items"]:
                identity = ProductIdentity(
                    sku=item["sku"],
                    mpn=item.get("mpn"),
                    model=item.get("model"),
                    gtin=item.get("gtin"),
                    variant_key=item.get("variant_key"),
                )
                effective, provenance = resolve_scope(
                    item["global_values"], item["store_values"]
                )
                upsert_record(
                    session,
                    tenant_id,
                    store_id,
                    identity,
                    effective,
                    provenance,
                    _parse_magento_datetime(item["updated_at"]),
                )
                for category_id in item["category_ids"]:
                    assign_product(session, tenant_id, item["sku"], category_id)
                report.records_written += 1

    session.commit()
    return report
```

- [ ] **Step 5: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/ingest/test_full_sync.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/skudo/magento/client.py src/skudo/ingest/ tests/ingest/
git commit -m "feat(s0): cliente del módulo y carga completa del catálogo por store view"
```

---

## Task 10: Módulo Magento — cola de cambios y deltas

Con "ritmo de cambio constante", los deltas no son una optimización: son el mecanismo
principal. Re-escanear 400k registros para encontrar los 300 que cambiaron es
insostenible.

**Files:**
- Create: `magento-module/Standard/Skudo/etc/db_schema.xml`
- Create: `magento-module/Standard/Skudo/etc/events.xml`
- Create: `magento-module/Standard/Skudo/Model/ChangeLog.php`
- Create: `magento-module/Standard/Skudo/Observer/ProductChanged.php`
- Create: `magento-module/Standard/Skudo/Api/DeltaReaderInterface.php`
- Create: `magento-module/Standard/Skudo/Model/DeltaReader.php`
- Modify: `magento-module/Standard/Skudo/etc/webapi.xml`, `etc/di.xml`
- Test: `magento-module/Standard/Skudo/Test/Unit/Observer/ProductChangedTest.php`

**Interfaces:**
- Consumes: `Cursor` (Task 8)
- Produces: `GET /rest/V1/skudo/deltas?sinceId=&limit=` devolviendo
  `{"items": [{"change_id": int, "sku": string, "event": "save"|"delete", "changed_at": string}], "last_change_id": int|null}`

- [ ] **Step 1: Declarar la tabla de la cola**

```xml
<!-- etc/db_schema.xml -->
<?xml version="1.0"?>
<schema xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:framework:Setup/Declaration/Schema/etc/schema.xsd">
    <table name="standard_skudo_change_log" resource="default" engine="innodb"
           comment="Cola de cambios de catálogo para StandardSkudo">
        <column xsi:type="int" name="change_id" unsigned="true" nullable="false"
                identity="true" comment="Id incremental del cambio"/>
        <column xsi:type="varchar" name="sku" length="255" nullable="false"/>
        <column xsi:type="varchar" name="event" length="16" nullable="false"
                comment="save|delete"/>
        <column xsi:type="timestamp" name="changed_at" on_update="false"
                default="CURRENT_TIMESTAMP" nullable="false"/>
        <constraint xsi:type="primary" referenceId="PRIMARY">
            <column name="change_id"/>
        </constraint>
        <index referenceId="STANDARD_SKUDO_CHANGE_LOG_SKU" indexType="btree">
            <column name="sku"/>
        </index>
    </table>
</schema>
```

- [ ] **Step 2: Declarar los eventos**

```xml
<!-- etc/events.xml -->
<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:noNamespaceSchemaLocation="urn:magento:framework:Event/etc/events.xsd">
    <event name="catalog_product_save_after">
        <observer name="standard_skudo_product_saved"
                  instance="Standard\Skudo\Observer\ProductChanged"/>
    </event>
    <event name="catalog_product_delete_after">
        <observer name="standard_skudo_product_deleted"
                  instance="Standard\Skudo\Observer\ProductChanged"/>
    </event>
</config>
```

- [ ] **Step 3: Escribir el test del observer que falla**

```php
<?php
// Test/Unit/Observer/ProductChangedTest.php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Observer;

use Magento\Framework\Event;
use Magento\Framework\Event\Observer;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\ChangeLog;
use Standard\Skudo\Observer\ProductChanged;

class ProductChangedTest extends TestCase
{
    public function testSaveIsRecordedAsSave(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('record')->with('SKU1', 'save');

        (new ProductChanged($log))->execute($this->observerFor('SKU1', 'catalog_product_save_after'));
    }

    public function testDeleteIsRecordedAsDelete(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->once())->method('record')->with('SKU1', 'delete');

        (new ProductChanged($log))->execute(
            $this->observerFor('SKU1', 'catalog_product_delete_after')
        );
    }

    public function testProductWithoutSkuIsIgnored(): void
    {
        $log = $this->createMock(ChangeLog::class);
        $log->expects($this->never())->method('record');

        (new ProductChanged($log))->execute($this->observerFor(null, 'catalog_product_save_after'));
    }

    private function observerFor(?string $sku, string $eventName): Observer
    {
        $product = new class ($sku) {
            public function __construct(private readonly ?string $sku)
            {
            }

            public function getSku(): ?string
            {
                return $this->sku;
            }
        };

        $event = $this->createMock(Event::class);
        $event->method('getName')->willReturn($eventName);
        $event->method('getData')->with('product')->willReturn($product);

        $observer = $this->createMock(Observer::class);
        $observer->method('getEvent')->willReturn($event);

        return $observer;
    }
}
```

- [ ] **Step 4: Ejecutar y comprobar que falla**

Run: `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: FAIL con `Class "Standard\Skudo\Observer\ProductChanged" not found`

- [ ] **Step 5: Implementar `Model/ChangeLog.php` y `Observer/ProductChanged.php`**

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;

class ChangeLog
{
    public const TABLE = 'standard_skudo_change_log';

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function record(string $sku, string $event): void
    {
        $connection = $this->resource->getConnection();
        $connection->insert(
            $this->resource->getTableName(self::TABLE),
            ['sku' => $sku, 'event' => $event]
        );
    }
}
```

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Standard\Skudo\Model\ChangeLog;

class ProductChanged implements ObserverInterface
{
    public function __construct(private readonly ChangeLog $log)
    {
    }

    public function execute(Observer $observer): void
    {
        $product = $observer->getEvent()->getData('product');
        $sku = $product?->getSku();

        if ($sku === null || $sku === '') {
            return;
        }

        $event = str_contains($observer->getEvent()->getName(), 'delete') ? 'delete' : 'save';
        $this->log->record((string) $sku, $event);
    }
}
```

- [ ] **Step 6: Implementar el lector de deltas**

```php
<?php
// Api/DeltaReaderInterface.php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface DeltaReaderInterface
{
    /**
     * @param int $sinceId
     * @param int $limit
     * @return mixed[]
     */
    public function getChanges(int $sinceId = 0, int $limit = 1000): array;
}
```

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Standard\Skudo\Api\DeltaReaderInterface;

class DeltaReader implements DeltaReaderInterface
{
    private const MAX_LIMIT = 5000;

    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function getChanges(int $sinceId = 0, int $limit = 1000): array
    {
        $limit = max(1, min($limit, self::MAX_LIMIT));
        $connection = $this->resource->getConnection();

        // Se pagina por change_id, que es monótono. Paginar por changed_at
        // perdería cambios cuando dos ocurren en el mismo segundo.
        $select = $connection->select()
            ->from($this->resource->getTableName(ChangeLog::TABLE))
            ->where('change_id > ?', $sinceId)
            ->order('change_id ASC')
            ->limit($limit);

        $rows = $connection->fetchAll($select);

        return [
            'items' => array_map(
                static fn (array $row): array => [
                    'change_id' => (int) $row['change_id'],
                    'sku' => (string) $row['sku'],
                    'event' => (string) $row['event'],
                    'changed_at' => (string) $row['changed_at'],
                ],
                $rows
            ),
            'last_change_id' => $rows === [] ? null : (int) end($rows)['change_id'],
        ];
    }
}
```

- [ ] **Step 7: Declarar ruta y preferencia**

Añadir a `etc/webapi.xml`:

```xml
    <route url="/V1/skudo/deltas" method="GET">
        <service class="Standard\Skudo\Api\DeltaReaderInterface" method="getChanges"/>
        <resources><resource ref="Standard_Skudo::read"/></resources>
    </route>
```

Añadir a `etc/di.xml`:

```xml
    <preference for="Standard\Skudo\Api\DeltaReaderInterface"
                type="Standard\Skudo\Model\DeltaReader"/>
```

- [ ] **Step 8: Ejecutar y comprobar que pasa**

Run: `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: 10 passed (3 EnvironmentProbe + 4 Cursor + 3 ProductChanged)

- [ ] **Step 9: Commit**

```bash
git add magento-module/
git commit -m "feat(s0): cola de cambios y endpoint de deltas paginado por change_id"
```

---

## Task 11: Ingestor — sincronización incremental con watermark

**Files:**
- Modify: `src/skudo/mirror/models.py` (añadir `SyncWatermark` al final)
- Modify: `src/skudo/magento/client.py` (añadir `iter_deltas` y `products_by_sku`)
- Create: `src/skudo/ingest/delta_sync.py`
- Create: `alembic/versions/0005_sync_watermark.py`
- Test: `tests/ingest/test_delta_sync.py`

**Interfaces:**
- Consumes: `MagentoClient` (Task 9); `upsert_record`, `get_record` (Task 6)
- Produces:
  - modelo `SyncWatermark`
  - `MagentoClient.iter_deltas(since_id: int, limit: int = 1000) -> Iterator[dict]`
  - `MagentoClient.products_by_sku(store_id: int, skus: list[str]) -> list[dict]`
  - `delta_sync(session, client, tenant_id, store_view_ids) -> DeltaSyncReport`
  - `DeltaSyncReport` (pydantic): `changes_seen: int`, `records_updated: int`,
    `records_deleted: int`, `watermark: int`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/ingest/test_delta_sync.py
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from skudo.ingest.delta_sync import delta_sync
from skudo.magento.client import MagentoClient
from skudo.mirror.models import SyncWatermark, Tenant
from skudo.mirror.products import ProductIdentity, get_record, upsert_record

FIXTURES = Path(__file__).parent.parent / "fixtures"

CHANGES = {
    "items": [
        {"change_id": 41, "sku": "SKU1", "event": "save",
         "changed_at": "2026-09-05 08:00:00"},
        {"change_id": 42, "sku": "SKU9", "event": "delete",
         "changed_at": "2026-09-05 08:01:00"},
    ],
    "last_change_id": 42,
}

REFRESHED = [
    {"sku": "SKU1", "mpn": None, "model": None, "gtin": None, "variant_key": None,
     "attribute_set_id": 4, "type_id": "simple",
     "global_values": {"name": "Notebook corregido"}, "store_values": {},
     "website_ids": [1], "category_ids": [], "updated_at": "2026-09-05 08:00:00"},
]


def make_client(changes=CHANGES) -> MagentoClient:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if path.endswith("/deltas"):
            since = int(request.url.params.get("sinceId", 0))
            if since >= (changes["last_change_id"] or 0):
                return httpx.Response(200, json={"items": [], "last_change_id": None})
            return httpx.Response(200, json=changes)
        if path.endswith("/products-by-sku"):
            return httpx.Response(200, json={"items": REFRESHED})
        return httpx.Response(404)

    return MagentoClient("https://x.test", "t", transport=httpx.MockTransport(handler))


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def seeded(db_session, tenant):
    from datetime import UTC, datetime

    for sku, name in (("SKU1", "Notebook"), ("SKU9", "A borrar")):
        upsert_record(db_session, tenant.id, 1,
                      ProductIdentity(sku=sku), {"name": name}, {"name": "global"},
                      datetime(2026, 9, 1, tzinfo=UTC))
    db_session.flush()
    return tenant


def test_saved_product_is_refreshed(db_session, seeded):
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU1", 1).attributes["name"] == "Notebook corregido"


def test_deleted_product_is_removed_from_the_mirror(db_session, seeded):
    report = delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    assert get_record(db_session, seeded.id, "SKU9", 1) is None
    assert report.records_deleted == 1


def test_watermark_advances_and_persists(db_session, seeded):
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    stored = db_session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == seeded.id)
    )
    assert stored == 42


def test_second_run_sees_no_changes(db_session, seeded):
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])
    second = delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    assert second.changes_seen == 0
    assert second.watermark == 42


def test_a_replayed_change_does_not_duplicate_records(db_session, seeded):
    from sqlalchemy import func
    from skudo.mirror.models import ProductRecord

    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])
    # Se fuerza el rebobinado del watermark para simular un reintento.
    db_session.execute(
        SyncWatermark.__table__.update()
        .where(SyncWatermark.tenant_id == seeded.id)
        .values(last_change_id=40)
    )
    delta_sync(db_session, make_client(), seeded.id, store_view_ids=[1])

    total = db_session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == seeded.id, ProductRecord.sku == "SKU1"
        )
    )
    assert total == 1
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/ingest/test_delta_sync.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.ingest.delta_sync'`

- [ ] **Step 3: Añadir el modelo al final de `src/skudo/mirror/models.py`**

```python
class SyncWatermark(Base):
    """Último change_id consumido por tenant. Es lo que hace incremental la sync."""

    __tablename__ = "sync_watermark"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    last_change_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Añadir los métodos al cliente en `src/skudo/magento/client.py`**

```python
    def iter_deltas(self, since_id: int, limit: int = 1000) -> Iterator[dict]:
        """Recorre la cola de cambios desde un watermark, página a página."""
        cursor = since_id
        while True:
            response = self._client.get("/deltas", params={"sinceId": cursor, "limit": limit})
            response.raise_for_status()
            page = response.json()

            if not page["items"]:
                return

            yield page
            cursor = page["last_change_id"]

    def products_by_sku(self, store_id: int, skus: list[str]) -> list[dict]:
        """Relee un conjunto concreto de SKUs. Complemento del endpoint de deltas:
        la cola dice QUÉ cambió, esto trae el estado nuevo."""
        if not skus:
            return []
        response = self._client.get(
            "/products-by-sku", params={"storeId": store_id, "skus": ",".join(skus)}
        )
        response.raise_for_status()
        return response.json()["items"]
```

> El endpoint `/V1/skudo/products-by-sku` se implementa en el módulo reutilizando
> `ProductReader::getPage`: misma proyección, filtrando `e.sku IN (?)` en lugar de
> paginar por cursor. Añadir a `Api/ProductReaderInterface.php` el método
> `getBySku(int $storeId, string $skus): array` que hace `explode(',', $skus)` y
> reutiliza los métodos privados existentes, más su ruta en `etc/webapi.xml`
> siguiendo el patrón de la Task 8, paso 7.

- [ ] **Step 5: Implementar `src/skudo/ingest/delta_sync.py`**

```python
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.ingest.full_sync import _parse_magento_datetime
from skudo.magento.client import MagentoClient
from skudo.mirror.models import ProductRecord, SyncWatermark
from skudo.mirror.products import ProductIdentity, resolve_scope, upsert_record


class DeltaSyncReport(BaseModel):
    changes_seen: int = 0
    records_updated: int = 0
    records_deleted: int = 0
    watermark: int = 0


def _read_watermark(session: Session, tenant_id: int) -> int:
    value = session.scalar(
        select(SyncWatermark.last_change_id).where(SyncWatermark.tenant_id == tenant_id)
    )
    return value or 0


def _write_watermark(session: Session, tenant_id: int, change_id: int) -> None:
    stmt = insert(SyncWatermark).values(tenant_id=tenant_id, last_change_id=change_id)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={"last_change_id": stmt.excluded.last_change_id},
        )
    )
    session.flush()


def delta_sync(
    session: Session,
    client: MagentoClient,
    tenant_id: int,
    store_view_ids: list[int],
) -> DeltaSyncReport:
    """Aplica los cambios pendientes desde el último watermark.

    El watermark solo avanza cuando la página se aplicó por completo: si algo
    falla a mitad, el reintento vuelve a traer esos cambios. Reaplicar un cambio
    es inofensivo porque todo el camino es upsert por
    (tenant, sku, store_view).
    """
    report = DeltaSyncReport(watermark=_read_watermark(session, tenant_id))

    for page in client.iter_deltas(report.watermark):
        # Un SKU puede aparecer varias veces en la misma página; solo interesa
        # su último estado, y un delete posterior gana a cualquier save previo.
        last_event: dict[str, str] = {}
        for change in page["items"]:
            report.changes_seen += 1
            last_event[change["sku"]] = change["event"]

        to_delete = [sku for sku, event in last_event.items() if event == "delete"]
        to_refresh = [sku for sku, event in last_event.items() if event != "delete"]

        if to_delete:
            result = session.execute(
                delete(ProductRecord).where(
                    ProductRecord.tenant_id == tenant_id,
                    ProductRecord.sku.in_(to_delete),
                )
            )
            report.records_deleted += result.rowcount or 0

        for store_id in store_view_ids:
            for item in client.products_by_sku(store_id, to_refresh):
                identity = ProductIdentity(
                    sku=item["sku"],
                    mpn=item.get("mpn"),
                    model=item.get("model"),
                    gtin=item.get("gtin"),
                    variant_key=item.get("variant_key"),
                )
                effective, provenance = resolve_scope(
                    item["global_values"], item["store_values"]
                )
                upsert_record(
                    session, tenant_id, store_id, identity, effective, provenance,
                    _parse_magento_datetime(item["updated_at"]),
                )
                report.records_updated += 1

        report.watermark = page["last_change_id"]
        _write_watermark(session, tenant_id, report.watermark)
        session.commit()

    return report
```

- [ ] **Step 6: Generar la migración**

Run: `SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo uv run alembic revision --autogenerate -m "sync watermark"`

Renombrar a `alembic/versions/0005_sync_watermark.py`.

- [ ] **Step 7: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/ingest/ -v`
Expected: 10 passed (5 de full_sync + 5 de delta_sync)

- [ ] **Step 8: Commit**

```bash
git add src/skudo/ alembic/versions/ tests/ingest/ magento-module/
git commit -m "feat(s0): sincronización incremental por deltas con watermark reintentable"
```

---

## Task 12: Señales comerciales

**Files:**
- Create: `magento-module/Standard/Skudo/Api/SignalReaderInterface.php`
- Create: `magento-module/Standard/Skudo/Model/SignalReader.php`
- Modify: `magento-module/Standard/Skudo/etc/webapi.xml`, `etc/di.xml`
- Modify: `src/skudo/mirror/models.py` (añadir `ProductSignal`)
- Create: `src/skudo/mirror/signals.py`
- Create: `alembic/versions/0006_product_signals.py`
- Test: `tests/mirror/test_signals.py`
- Test: `magento-module/Standard/Skudo/Test/Unit/Model/SignalReaderTest.php`

**Interfaces:**
- Consumes: `Base`, `Tenant` (Task 4)
- Produces:
  - modelo `ProductSignal`
  - `upsert_signals(session, tenant_id, store_view_magento_id, rows: list[dict]) -> int`
  - `get_signal(session, tenant_id, sku, store_view_magento_id) -> ProductSignal | None`
  - `GET /rest/V1/skudo/signals?storeId=&days=` devolviendo
    `{"items": [{sku, units_sold, revenue, salable_qty, physical_qty, uses_msi, margin, search_demand}]}`

- [ ] **Step 1: Escribir los tests Python que fallan**

```python
# tests/mirror/test_signals.py
import pytest

from skudo.mirror.models import Tenant
from skudo.mirror.signals import get_signal, upsert_signals


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


ROWS = [
    {"sku": "SKU1", "units_sold": 312, "revenue": 45000000.0, "salable_qty": 4.0,
     "physical_qty": 9.0, "uses_msi": True, "margin": 0.22, "search_demand": 890},
    {"sku": "SKU2", "units_sold": 0, "revenue": 0.0, "salable_qty": 0.0,
     "physical_qty": 0.0, "uses_msi": True, "margin": None, "search_demand": 3},
]


def test_signals_are_stored_per_store_view(db_session, tenant):
    assert upsert_signals(db_session, tenant.id, 1, ROWS) == 2

    row = get_signal(db_session, tenant.id, "SKU1", 1)
    assert row.units_sold == 312
    assert get_signal(db_session, tenant.id, "SKU1", 2) is None


def test_salable_quantity_is_kept_apart_from_physical(db_session, tenant):
    """Con MSI, lo que se puede vender no es lo que hay en el almacén: la
    diferencia son reservas y pedidos pendientes. Priorizar por cantidad física
    haría enriquecer productos que en realidad no se pueden vender."""
    upsert_signals(db_session, tenant.id, 1, ROWS)

    row = get_signal(db_session, tenant.id, "SKU1", 1)
    assert row.salable_qty == 4.0
    assert row.physical_qty == 9.0
    assert row.uses_msi is True


def test_missing_margin_is_preserved_as_unknown(db_session, tenant):
    """Margen ausente es 'desconocido', no cero. Colapsarlo falsearía la
    priorización comercial."""
    upsert_signals(db_session, tenant.id, 1, ROWS)

    assert get_signal(db_session, tenant.id, "SKU2", 1).margin is None


def test_upsert_replaces_previous_window(db_session, tenant):
    upsert_signals(db_session, tenant.id, 1, ROWS)
    upsert_signals(db_session, tenant.id, 1, [{**ROWS[0], "units_sold": 400}])

    assert get_signal(db_session, tenant.id, "SKU1", 1).units_sold == 400
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/mirror/test_signals.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.mirror.signals'`

- [ ] **Step 3: Añadir el modelo al final de `src/skudo/mirror/models.py`**

```python
class ProductSignal(Base):
    """Señales comerciales por (tenant, sku, store view).

    `margin` y `salable_qty` admiten NULL a propósito: 'desconocido' no es 'cero'.
    """

    __tablename__ = "product_signal"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", "store_view_magento_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    sku: Mapped[str] = mapped_column(String(255), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)

    units_sold: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    salable_qty: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    physical_qty: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    uses_msi: Mapped[bool] = mapped_column(Boolean, default=False)
    margin: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    search_demand: Mapped[int] = mapped_column(Integer, default=0)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Añadir `Numeric` al import de `sqlalchemy` en la cabecera del archivo.

- [ ] **Step 4: Implementar `src/skudo/mirror/signals.py`**

```python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductSignal

_UPDATABLE = (
    "units_sold", "revenue", "salable_qty", "physical_qty",
    "uses_msi", "margin", "search_demand",
)


def upsert_signals(
    session: Session, tenant_id: int, store_view_magento_id: int, rows: list[dict]
) -> int:
    if not rows:
        return 0

    values = [
        {
            "tenant_id": tenant_id,
            "store_view_magento_id": store_view_magento_id,
            "sku": row["sku"],
            **{key: row.get(key) for key in _UPDATABLE},
        }
        for row in rows
    ]
    stmt = insert(ProductSignal).values(values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku", "store_view_magento_id"],
            set_={key: getattr(stmt.excluded, key) for key in _UPDATABLE},
        )
    )
    session.flush()
    return len(values)


def get_signal(
    session: Session, tenant_id: int, sku: str, store_view_magento_id: int
) -> ProductSignal | None:
    return session.scalar(
        select(ProductSignal).where(
            ProductSignal.tenant_id == tenant_id,
            ProductSignal.sku == sku,
            ProductSignal.store_view_magento_id == store_view_magento_id,
        )
    )
```

- [ ] **Step 5: Ejecutar los tests Python y comprobar que pasan**

Run: `SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo uv run alembic revision --autogenerate -m "product signals"` (renombrar a `0006_product_signals.py`) y después
`uv run pytest tests/mirror/test_signals.py -v`
Expected: 4 passed

- [ ] **Step 6: Escribir el test PHP del fallback de MSI**

```php
<?php
// Test/Unit/Model/SignalReaderTest.php
declare(strict_types=1);

namespace Standard\Skudo\Test\Unit\Model;

use Magento\Framework\Module\ModuleListInterface;
use PHPUnit\Framework\TestCase;
use Standard\Skudo\Model\SignalReader;

class SignalReaderTest extends TestCase
{
    public function testMsiAbsentFallsBackToPhysicalQuantity(): void
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->with('Magento_InventoryApi')->willReturn(false);

        $this->assertFalse(SignalReader::usesMsi($modules));
    }

    public function testMsiPresentUsesSalableQuantity(): void
    {
        $modules = $this->createMock(ModuleListInterface::class);
        $modules->method('has')->with('Magento_InventoryApi')->willReturn(true);

        $this->assertTrue(SignalReader::usesMsi($modules));
    }
}
```

- [ ] **Step 7: Implementar `Model/SignalReader.php`**

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Magento\Framework\Module\ModuleListInterface;
use Standard\Skudo\Api\SignalReaderInterface;

class SignalReader implements SignalReaderInterface
{
    public function __construct(
        private readonly ResourceConnection $resource,
        private readonly ModuleListInterface $modules,
    ) {
    }

    public static function usesMsi(ModuleListInterface $modules): bool
    {
        return $modules->has('Magento_InventoryApi');
    }

    public function getSignals(int $storeId, int $days = 90): array
    {
        $connection = $this->resource->getConnection();
        $usesMsi = self::usesMsi($this->modules);

        $sales = $connection->select()
            ->from(['oi' => $this->resource->getTableName('sales_order_item')], [
                'sku' => 'oi.sku',
                'units_sold' => 'SUM(oi.qty_ordered)',
                'revenue' => 'SUM(oi.row_total_incl_tax)',
            ])
            ->join(
                ['o' => $this->resource->getTableName('sales_order')],
                'o.entity_id = oi.order_id',
                []
            )
            ->where('o.store_id = ?', $storeId)
            ->where('o.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)', $days)
            ->group('oi.sku');

        $rows = [];
        foreach ($connection->fetchAll($sales) as $row) {
            $rows[(string) $row['sku']] = [
                'sku' => (string) $row['sku'],
                'units_sold' => (int) $row['units_sold'],
                'revenue' => (float) $row['revenue'],
                'salable_qty' => null,
                'physical_qty' => null,
                'uses_msi' => $usesMsi,
                'margin' => null,
                'search_demand' => 0,
            ];
        }

        $this->attachInventory($rows, $usesMsi);
        $this->attachSearchDemand($rows, $storeId, $days);

        return ['items' => array_values($rows)];
    }

    /**
     * Cantidad física siempre; vendible solo si MSI está activo.
     * Se devuelven las dos por separado para que el ingestor no tenga que
     * adivinar cuál está mirando.
     */
    private function attachInventory(array &$rows, bool $usesMsi): void
    {
        if ($rows === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        $select = $connection->select()
            ->from(
                ['si' => $this->resource->getTableName('cataloginventory_stock_item')],
                ['qty' => 'si.qty']
            )
            ->join(
                ['e' => $this->resource->getTableName('catalog_product_entity')],
                'e.entity_id = si.product_id',
                ['sku' => 'e.sku']
            )
            ->where('e.sku IN (?)', array_keys($rows));

        foreach ($connection->fetchAll($select) as $row) {
            $sku = (string) $row['sku'];
            $rows[$sku]['physical_qty'] = (float) $row['qty'];
        }

        if (!$usesMsi) {
            return;
        }

        $salableTable = $this->resource->getTableName('inventory_stock_1');
        if (!$connection->isTableExists($salableTable)) {
            return;
        }

        $salable = $connection->select()
            ->from(['s' => $salableTable], ['sku' => 's.sku', 'qty' => 's.quantity'])
            ->where('s.sku IN (?)', array_keys($rows));

        foreach ($connection->fetchAll($salable) as $row) {
            $rows[(string) $row['sku']]['salable_qty'] = (float) $row['qty'];
        }
    }

    private function attachSearchDemand(array &$rows, int $storeId, int $days): void
    {
        if ($rows === []) {
            return;
        }

        $connection = $this->resource->getConnection();
        $select = $connection->select()
            ->from(
                $this->resource->getTableName('search_query'),
                ['query_text', 'popularity', 'num_results']
            )
            ->where('store_id = ?', $storeId)
            ->where('updated_at >= DATE_SUB(NOW(), INTERVAL ? DAY)', $days);

        // La demanda se atribuye por coincidencia del SKU en el término buscado:
        // es la única atribución que el core permite sin un índice de clics.
        foreach ($connection->fetchAll($select) as $row) {
            $text = strtolower((string) $row['query_text']);
            foreach ($rows as $sku => $_) {
                if (str_contains($text, strtolower((string) $sku))) {
                    $rows[$sku]['search_demand'] += (int) $row['popularity'];
                }
            }
        }
    }
}
```

Y su interfaz:

```php
<?php
// Api/SignalReaderInterface.php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface SignalReaderInterface
{
    /**
     * @param int $storeId
     * @param int $days
     * @return mixed[]
     */
    public function getSignals(int $storeId, int $days = 90): array;
}
```

Ruta en `etc/webapi.xml` y preferencia en `etc/di.xml`, siguiendo el patrón de la
Task 8, paso 7, con `url="/V1/skudo/signals"` y método `getSignals`.

- [ ] **Step 8: Ejecutar los tests PHP y comprobar que pasan**

Run: `vendor/bin/phpunit -c dev/tests/unit/phpunit.xml.dist app/code/Standard/Skudo/Test/Unit`
Expected: 12 passed

- [ ] **Step 9: Commit**

```bash
git add src/skudo/ alembic/versions/ tests/mirror/test_signals.py magento-module/
git commit -m "feat(s0): señales comerciales con disponibilidad vendible y margen desconocido"
```

---

## Task 13: Reconciliación y detección de deriva

Los deltas pueden perderse: una cola truncada, un observer que no disparó en una
importación masiva por SQL directo, un reintento fallido. Sin reconciliación el espejo
se desvía en silencio, y todos los scores de S1 heredan ese error sin que nadie lo note.

**Files:**
- Create: `magento-module/Standard/Skudo/Api/ChecksumReaderInterface.php`
- Create: `magento-module/Standard/Skudo/Model/ChecksumReader.php`
- Modify: `magento-module/Standard/Skudo/etc/webapi.xml`, `etc/di.xml`
- Modify: `src/skudo/magento/client.py` (añadir `checksums`)
- Create: `src/skudo/ingest/reconcile.py`
- Test: `tests/ingest/test_reconcile.py`

**Interfaces:**
- Consumes: `MagentoClient` (Task 9); `ProductRecord` (Task 6)
- Produces:
  - `GET /rest/V1/skudo/checksums?storeId=` devolviendo
    `{"product_count": int, "sku_digest": string}`
  - `MagentoClient.checksums(store_id: int) -> dict`
  - `DriftReport` (pydantic): `store_view_magento_id: int`, `magento_count: int`,
    `mirror_count: int`, `digest_matches: bool`, `needs_full_sync: bool`
  - `reconcile(session, client, tenant_id, store_view_magento_id) -> DriftReport`

- [ ] **Step 1: Escribir los tests que fallan**

```python
# tests/ingest/test_reconcile.py
import hashlib

import httpx
import pytest

from skudo.ingest.reconcile import reconcile, sku_digest
from skudo.magento.client import MagentoClient
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, upsert_record


def make_client(count: int, digest: str) -> MagentoClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/checksums"):
            return httpx.Response(200, json={"product_count": count, "sku_digest": digest})
        return httpx.Response(404)

    return MagentoClient("https://x.test", "t", transport=httpx.MockTransport(handler))


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def mirrored(db_session, tenant):
    from datetime import UTC, datetime

    for sku in ("SKU1", "SKU2"):
        upsert_record(db_session, tenant.id, 1, ProductIdentity(sku=sku),
                      {"name": sku}, {"name": "global"}, datetime(2026, 9, 1, tzinfo=UTC))
    db_session.flush()
    return tenant


def test_digest_is_order_independent():
    assert sku_digest(["SKU2", "SKU1"]) == sku_digest(["SKU1", "SKU2"])


def test_digest_changes_when_a_sku_is_missing():
    assert sku_digest(["SKU1", "SKU2"]) != sku_digest(["SKU1"])


def test_no_drift_when_count_and_digest_match(db_session, mirrored):
    report = reconcile(db_session, make_client(2, sku_digest(["SKU1", "SKU2"])),
                       mirrored.id, 1)

    assert report.digest_matches is True
    assert report.needs_full_sync is False


def test_drift_detected_when_magento_has_more_products(db_session, mirrored):
    report = reconcile(db_session, make_client(3, "cualquier-otro-digest"), mirrored.id, 1)

    assert report.magento_count == 3
    assert report.mirror_count == 2
    assert report.needs_full_sync is True


def test_drift_detected_when_counts_match_but_content_differs(db_session, mirrored):
    """El conteo puede coincidir y el contenido no: un SKU borrado y otro creado
    en el mismo intervalo. Por eso hace falta el digest y no solo contar."""
    report = reconcile(db_session, make_client(2, sku_digest(["SKU1", "SKU3"])),
                       mirrored.id, 1)

    assert report.magento_count == report.mirror_count
    assert report.digest_matches is False
    assert report.needs_full_sync is True
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/ingest/test_reconcile.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.ingest.reconcile'`

- [ ] **Step 3: Implementar `src/skudo/ingest/reconcile.py`**

```python
import hashlib

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.magento.client import MagentoClient
from skudo.mirror.models import ProductRecord


class DriftReport(BaseModel):
    store_view_magento_id: int
    magento_count: int
    mirror_count: int
    digest_matches: bool
    needs_full_sync: bool


def sku_digest(skus: list[str]) -> str:
    """Huella del conjunto de SKUs, independiente del orden.

    Se ordena antes de hashear para que Magento y el espejo puedan calcularla
    por separado y comparar sin coordinar paginación.
    """
    joined = "\n".join(sorted(skus))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def reconcile(
    session: Session, client: MagentoClient, tenant_id: int, store_view_magento_id: int
) -> DriftReport:
    remote = client.checksums(store_view_magento_id)

    local_skus = list(
        session.scalars(
            select(ProductRecord.sku).where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
            )
        ).all()
    )

    digest_matches = sku_digest(local_skus) == remote["sku_digest"]
    count_matches = len(local_skus) == remote["product_count"]

    return DriftReport(
        store_view_magento_id=store_view_magento_id,
        magento_count=remote["product_count"],
        mirror_count=len(local_skus),
        digest_matches=digest_matches,
        # Cualquier discrepancia obliga a recarga completa: no se intenta
        # reparar por diferencias parciales, porque no sabemos qué más falta.
        needs_full_sync=not (digest_matches and count_matches),
    )
```

- [ ] **Step 4: Añadir `checksums` al cliente en `src/skudo/magento/client.py`**

```python
    def checksums(self, store_id: int) -> dict:
        response = self._client.get("/checksums", params={"storeId": store_id})
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 5: Implementar el lado Magento**

```php
<?php
// Api/ChecksumReaderInterface.php
declare(strict_types=1);

namespace Standard\Skudo\Api;

interface ChecksumReaderInterface
{
    /**
     * @param int $storeId
     * @return mixed[]
     */
    public function getChecksums(int $storeId): array;
}
```

```php
<?php
declare(strict_types=1);

namespace Standard\Skudo\Model;

use Magento\Framework\App\ResourceConnection;
use Standard\Skudo\Api\ChecksumReaderInterface;

class ChecksumReader implements ChecksumReaderInterface
{
    public function __construct(private readonly ResourceConnection $resource)
    {
    }

    public function getChecksums(int $storeId): array
    {
        $connection = $this->resource->getConnection();
        $entity = $this->resource->getTableName('catalog_product_entity');

        // Se ordena y se concatena en la misma forma que sku_digest en Python:
        // ambos lados tienen que calcular la MISMA huella para poder compararla.
        $skus = $connection->fetchCol(
            $connection->select()->from($entity, ['sku'])->order('sku ASC')
        );

        sort($skus, SORT_STRING);

        return [
            'product_count' => count($skus),
            'sku_digest' => hash('sha256', implode("\n", $skus)),
        ];
    }
}
```

Ruta `url="/V1/skudo/checksums"` con método `getChecksums` en `etc/webapi.xml`, y su
preferencia en `etc/di.xml`, siguiendo el patrón de la Task 8, paso 7.

> **Nota de consistencia:** `sort($skus, SORT_STRING)` en PHP y `sorted(skus)` en
> Python tienen que producir el mismo orden. Ambos ordenan por bytes de la cadena,
> así que coinciden para SKUs ASCII. El test de la Task 14 lo verifica contra la
> instancia real; si el catálogo tiene SKUs con acentos o caracteres no ASCII y el
> digest no cuadra, la causa es la colación y hay que normalizar a bytes en los dos
> lados antes de ordenar.

- [ ] **Step 6: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/ingest/test_reconcile.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add src/skudo/ tests/ingest/test_reconcile.py magento-module/
git commit -m "feat(s0): reconciliación por conteo y huella de SKUs con deriva detectada"
```

---

## Task 14: Arnés de aceptación de S0

Convierte los cuatro criterios de aceptación del spec en un comando ejecutable, para
que "S0 está listo" sea una salida verificable y no una opinión.

**Files:**
- Create: `src/skudo/acceptance/__init__.py`, `src/skudo/acceptance/s0.py`
- Create: `docs/superpowers/plans/s0-verificacion-manual.md`
- Test: `tests/acceptance/test_s0_harness.py`

**Interfaces:**
- Consumes: todo lo anterior
- Produces:
  - `CriterionResult` (pydantic): `name: str`, `passed: bool`, `detail: str`
  - `run_s0_acceptance(session, client, tenant_id, store_view_ids) -> list[CriterionResult]`
  - CLI: `python -m skudo.acceptance.s0 --tenant nissei`

- [ ] **Step 1: Escribir el test del arnés que falla**

```python
# tests/acceptance/test_s0_harness.py
import json
from pathlib import Path

import httpx
import pytest

from skudo.acceptance.s0 import run_s0_acceptance
from skudo.magento.client import MagentoClient
from skudo.mirror.attributes import upsert_attribute, upsert_option
from skudo.mirror.models import Tenant
from skudo.mirror.products import ProductIdentity, upsert_record
from skudo.ingest.reconcile import sku_digest

FIXTURES = Path(__file__).parent.parent / "fixtures"


def make_client(skus: list[str]) -> MagentoClient:
    environment = json.loads((FIXTURES / "environment_opensource.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if request.url.path.endswith("/checksums"):
            return httpx.Response(
                200, json={"product_count": len(skus), "sku_digest": sku_digest(skus)}
            )
        return httpx.Response(404)

    return MagentoClient("https://x.test", "t", transport=httpx.MockTransport(handler))


@pytest.fixture
def prepared(db_session):
    from datetime import UTC, datetime

    tenant = Tenant(code="nissei", name="Nissei", base_url="https://x.test",
                    token_env_var="T")
    db_session.add(tenant)
    db_session.flush()

    for store_id in (1, 2):
        upsert_record(db_session, tenant.id, store_id, ProductIdentity(sku="SKU1"),
                      {"name": "N"}, {"name": "store"},
                      datetime(2026, 9, 1, tzinfo=UTC))

    upsert_attribute(db_session, tenant.id, {
        "code": "color", "label": "Color", "frontend_input": "select",
        "declared_scope": "store", "is_filterable": True, "is_required": False,
        "attribute_set_ids": [4],
    })
    upsert_option(db_session, tenant.id, "color", 17, {1: "Negro", 2: "Preto"})
    db_session.flush()
    return tenant


def test_all_four_criteria_pass_on_a_healthy_mirror(db_session, prepared):
    results = run_s0_acceptance(db_session, make_client(["SKU1"]), prepared.id, [1, 2])

    assert [r.name for r in results] == [
        "espejo_sincronizado", "score_por_store_view",
        "procedencia_de_scope", "identidad_de_opciones",
    ]
    assert all(r.passed for r in results), [r.detail for r in results if not r.passed]


def test_drift_makes_the_first_criterion_fail(db_session, prepared):
    results = run_s0_acceptance(
        db_session, make_client(["SKU1", "SKU-FANTASMA"]), prepared.id, [1, 2]
    )

    failed = {r.name: r for r in results if not r.passed}
    assert "espejo_sincronizado" in failed
    assert "2" in failed["espejo_sincronizado"].detail
```

- [ ] **Step 2: Ejecutar y comprobar que falla**

Run: `uv run pytest tests/acceptance/test_s0_harness.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.acceptance'`

- [ ] **Step 3: Implementar `src/skudo/acceptance/s0.py`**

```python
"""Arnés de aceptación de S0.

Cada criterio del spec se comprueba contra el espejo real. La salida es una
lista de resultados, no un booleano: cuando algo falla hay que saber qué.
"""

import argparse
import sys

from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from skudo.config import Settings
from skudo.ingest.reconcile import reconcile
from skudo.magento.client import MagentoClient
from skudo.mirror.attributes import distinct_option_ids, option_labels
from skudo.mirror.models import Attribute, ProductRecord, Tenant


class CriterionResult(BaseModel):
    name: str
    passed: bool
    detail: str


def _espejo_sincronizado(session, client, tenant_id, store_view_ids) -> CriterionResult:
    drifted = []
    for store_id in store_view_ids:
        report = reconcile(session, client, tenant_id, store_id)
        if report.needs_full_sync:
            drifted.append(
                f"store {store_id}: magento={report.magento_count} "
                f"espejo={report.mirror_count} digest_ok={report.digest_matches}"
            )
    return CriterionResult(
        name="espejo_sincronizado",
        passed=not drifted,
        detail="sin deriva" if not drifted else "; ".join(drifted),
    )


def _score_por_store_view(session, tenant_id, store_view_ids) -> CriterionResult:
    """Todo SKU del espejo debe existir en todas las store views declaradas.

    Si falta en una, el sistema no podría dar dos grados distintos para el mismo
    producto, que es el requisito central del objeto evaluable.
    """
    counts = dict(
        session.execute(
            select(ProductRecord.store_view_magento_id, func.count())
            .where(ProductRecord.tenant_id == tenant_id)
            .group_by(ProductRecord.store_view_magento_id)
        ).all()
    )
    missing = [s for s in store_view_ids if s not in counts]
    uneven = len(set(counts.values())) > 1

    return CriterionResult(
        name="score_por_store_view",
        passed=not missing and not uneven,
        detail=f"conteos por store view: {counts}"
        + (f"; faltan {missing}" if missing else "")
        + ("; conteos desiguales entre tiendas" if uneven else ""),
    )


def _procedencia_de_scope(session, tenant_id) -> CriterionResult:
    """Toda clave de `attributes` debe tener su entrada en `scope_provenance`."""
    rows = session.scalars(
        select(ProductRecord).where(ProductRecord.tenant_id == tenant_id).limit(500)
    ).all()

    broken = [
        r.sku for r in rows if set(r.attributes.keys()) != set(r.scope_provenance.keys())
    ]
    return CriterionResult(
        name="procedencia_de_scope",
        passed=not broken,
        detail=f"{len(rows)} registros revisados"
        + (f"; sin procedencia completa: {broken[:5]}" if broken else ""),
    )


def _identidad_de_opciones(session, tenant_id) -> CriterionResult:
    """Una opción con etiquetas distintas por tienda sigue siendo UNA opción."""
    codes = session.scalars(
        select(Attribute.code).where(
            Attribute.tenant_id == tenant_id, Attribute.frontend_input == "select"
        )
    ).all()

    translated = 0
    for code in codes:
        for option_id in distinct_option_ids(session, tenant_id, code):
            labels = option_labels(session, tenant_id, code, option_id)
            store_labels = {v for k, v in labels.items() if k != 0}
            if len(store_labels) > 1:
                translated += 1

    return CriterionResult(
        name="identidad_de_opciones",
        passed=translated > 0,
        detail=f"{translated} opciones con etiqueta distinta por store view "
        "reconocidas como una sola opción",
    )


def run_s0_acceptance(
    session: Session, client: MagentoClient, tenant_id: int, store_view_ids: list[int]
) -> list[CriterionResult]:
    return [
        _espejo_sincronizado(session, client, tenant_id, store_view_ids),
        _score_por_store_view(session, tenant_id, store_view_ids),
        _procedencia_de_scope(session, tenant_id),
        _identidad_de_opciones(session, tenant_id),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica los criterios de S0")
    parser.add_argument("--tenant", required=True, help="código del tenant")
    parser.add_argument("--stores", required=True,
                        help="ids de store view separados por coma, p.ej. 1,2")
    args = parser.parse_args()

    settings = Settings()
    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        tenant = session.scalar(select(Tenant).where(Tenant.code == args.tenant))
        if tenant is None:
            print(f"tenant desconocido: {args.tenant}", file=sys.stderr)
            return 2

        client = MagentoClient(tenant.base_url, settings.tenant_token(tenant.code))
        results = run_s0_acceptance(
            session, client, tenant.id, [int(s) for s in args.stores.split(",")]
        )

    for result in results:
        print(f"[{'OK ' if result.passed else 'FALLA'}] {result.name}: {result.detail}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Ejecutar y comprobar que pasa**

Run: `uv run pytest tests/acceptance/test_s0_harness.py -v`
Expected: 2 passed

- [ ] **Step 5: Escribir la checklist de verificación manual**

Crear `docs/superpowers/plans/s0-verificacion-manual.md`:

```markdown
# S0 — Verificación manual contra la instancia real

El arnés automático (`python -m skudo.acceptance.s0`) cubre los cuatro criterios
del spec. Estas cinco comprobaciones necesitan ojo humano y se hacen una vez,
antes de declarar S0 terminado.

1. **Procedencia de scope, muestra a mano.** Elegir 10 productos con override en BR
   y verificar en el admin de Magento que el valor que el espejo marca como `store`
   es exactamente el que la store view de BR muestra, y que el marcado como `global`
   no tiene override.
2. **Efecto de categoría.** Elegir un producto asignado a una categoría que cuelga
   del árbol de PY y comprobar que `derive_category_effect` devuelve
   `fuera_del_arbol_de_la_tienda` para la store view de BR. Comprobarlo también en
   el frontend: el producto no debe aparecer en esa navegación.
3. **Opción traducida.** Localizar un atributo select con etiquetas distintas en PY y
   BR y confirmar que el espejo guarda un solo `option_id` con dos labels.
4. **SLA de delta.** Editar el nombre de un producto en el admin, ejecutar
   `delta_sync` y medir cuánto tarda el cambio en aparecer en el espejo. Anotar el
   número: es la línea base del SLA.
5. **Digest de reconciliación.** Ejecutar `reconcile` sobre el catálogo completo. Si
   el digest no cuadra con conteos iguales, revisar la colación de ordenación de SKUs
   entre PHP y Python (ver la nota de la Task 13).

Anotar los resultados en este archivo con fecha antes de pasar a S1.
```

- [ ] **Step 6: Ejecutar la suite completa**

Run: `make test && uv run ruff check src tests`
Expected: 45 passed, ruff sin hallazgos

- [ ] **Step 7: Commit**

```bash
git add src/skudo/acceptance/ tests/acceptance/ docs/superpowers/plans/s0-verificacion-manual.md
git commit -m "feat(s0): arnés ejecutable de los criterios de aceptación y checklist manual"
```

---

## Notas de la auto-revisión

Revisión del plan contra el spec, hecha después de escribirlo.

**Cobertura del spec (sección S0).** Los cuatro criterios de aceptación tienen tarea:
espejo sincronizado (Tasks 9, 11, 13, 14), cambio dentro del SLA de delta (Tasks 10,
11), procedencia de scope y efecto de categoría verificados a mano (Tasks 6, 7 y la
checklist manual), opción traducida reconocida como una (Task 5). Las exigencias del
modelo de datos de la sección 5.6 del spec que corresponden a S0 —procedencia de scope,
identidad desglosada, `option_id` con labels, asignación global más efecto derivado,
disponibilidad vendible— están en las Tasks 5, 6, 7 y 12.

**Fuera de este plan a propósito.** Endpoints de escritura, `evidence`, `findings`,
`check_coverage`, `concept_map`, la sonda de superficie publicada y las tablas de
remediación pertenecen a S1–S6. La tabla `EnvironmentSnapshot` sí entra en S0 aunque el
spec no la pida: es el mecanismo que detecta que el Magento del cliente cambió de
versión o activó Staging bajo nuestros pies, y sin ella la clave de entidad descubierta
en la Task 2 podría quedar obsoleta en silencio.

**Consistencia de tipos verificada.** `ProductIdentity` se construye en las Tasks 6, 9,
11 y 14 siempre con los mismos campos. `resolve_scope` devuelve
`(effective, provenance)` en ese orden en las Tasks 6, 9 y 11. `sku_digest` se calcula
con `sorted()` en Python y `sort($skus, SORT_STRING)` en PHP, y la divergencia posible
por colación queda documentada como riesgo con su comprobación en la checklist manual.
`product_entity_key` se lee del perfil en las Tasks 2, 3 y 8 con los mismos dos valores
posibles.

**Riesgo conocido que este plan no cierra.** El endpoint `/products-by-sku` de la Task
11 se describe por reutilización de `ProductReader` en lugar de con su código completo,
porque es la misma proyección con otro `WHERE`. Si el implementador de la Task 11 no
tiene delante la Task 8, conviene que la lea antes de empezar.

**Dependencia de entorno.** Las tareas 3, 8, 10, 12 y 13 incluyen código PHP cuyos tests
unitarios necesitan una instalación de Magento con el módulo enlazado en
`app/code/Standard/Skudo`. Si no hay instancia disponible, esas tareas quedan
bloqueadas en su paso de verificación: el código se puede escribir, pero **no se puede
declarar hecho**. Conseguir la instancia de desarrollo es un prerrequisito real de S0,
no un detalle.
