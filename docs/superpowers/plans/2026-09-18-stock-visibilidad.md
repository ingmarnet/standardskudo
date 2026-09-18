# Stock y visibilidad en la nota — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un producto sin stock que Magento oculta no penalice la nota, uno sin stock pero visible cuente con menor prioridad (de orden), y uno con stock tenga prioridad plena — sin inventar nunca un "sin stock" a partir de un dato ausente.

**Architecture:** Un cuarto factor del estado de vitrina de la `Ficha`, derivado de `is_in_stock` (por producto) + `show_out_of_stock` (por store view). Un `oculto_sin_stock` deja de ser `PUBLICADO`, así que cae solo del scoring y de la evaluación por la misma puerta que hoy usan los no publicados. Todo el lado Python se construye y prueba con fixtures; el módulo PHP y la sincronización real son un plan posterior, bloqueado por la red al Magento.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, FastAPI, Postgres. Panel: SPA de un archivo (`src/skudo/web/static/index.html`).

**Spec:** `docs/superpowers/specs/2026-09-18-stock-visibilidad-design.md`

## Global Constraints

- **La ignorancia gana:** un producto solo se oculta de la nota cuando `is_in_stock is False` Y `muestra_sin_stock is False`, ambos explícitos. Si cualquiera de los dos es `None`, el producto se comporta EXACTAMENTE como hoy. Ningún `None` se lee como "sin stock".
- **Producción es solo lectura** (no aplica a este plan, que es todo Python + fixtures, pero rige el plan del módulo).
- El predicado de "publicado" es UNO solo (`_publicado`/`Ficha.estado`); scoring y detectores lo comparten, nunca se copia.
- Migración nueva: `0021`, `down_revision = "0020"`. Registrar cualquier modelo nuevo en `alembic/env.py`.
- `NULL` ≠ `0` en el espejo: columnas de medición son `nullable=True` sin `default`.
- Tests de sabotaje e invariantes de objeto entero; orden determinista en todo lo que se persiste o lista.

---

### Task 1: Migración 0021 y modelos (is_in_stock, store_setting, prioridad_vitrina)

**Files:**
- Modify: `src/skudo/mirror/models.py` (columna `ProductSignal.is_in_stock`; nueva clase `StoreSetting`)
- Modify: `src/skudo/score/models.py` (columna `ProductScore.prioridad_vitrina`)
- Create: `alembic/versions/0021_stock_y_visibilidad.py`
- Modify: `alembic/env.py` (si `StoreSetting` no queda cubierto por el import de `skudo.mirror.models`, verificar que sí)
- Test: `tests/mirror/test_store_setting.py`

**Interfaces:**
- Produces: `ProductSignal.is_in_stock: bool | None`; `StoreSetting(tenant_id, store_view_magento_id, key, value, synced_at)` con única `(tenant_id, store_view_magento_id, key)`; `ProductScore.prioridad_vitrina: str | None`.

- [ ] **Step 1: Escribir el test que falla** (`tests/mirror/test_store_setting.py`)

```python
from datetime import UTC, datetime

from skudo.mirror.models import ProductSignal, StoreSetting, Tenant


def test_store_setting_unica_por_tenant_store_key(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                key="show_out_of_stock", value="false"))
    db_session.flush()
    fila = db_session.query(StoreSetting).filter_by(tenant_id=t.id, store_view_magento_id=1,
                                                    key="show_out_of_stock").one()
    assert fila.value == "false"


def test_product_signal_is_in_stock_admite_null(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(ProductSignal(tenant_id=t.id, sku="1007", store_view_magento_id=1,
                                 uses_msi=False, is_in_stock=None))
    db_session.flush()
    assert db_session.query(ProductSignal).filter_by(sku="1007").one().is_in_stock is None
```

- [ ] **Step 2: Correr y ver que falla**

Run: `.venv/bin/python -m pytest tests/mirror/test_store_setting.py -q`
Expected: FAIL con `ImportError` (`StoreSetting` no existe) / `is_in_stock` desconocido.

- [ ] **Step 3: Añadir la columna a `ProductSignal`** en `src/skudo/mirror/models.py`, junto a `salable_qty`/`physical_qty`:

```python
    # El flag con que Magento decide OCULTAR (no el qty): puede haber qty 0 con
    # is_in_stock=1 (backorders). NULL = no hay fila de stock / no se sincronizó;
    # NUNCA se lee como "sin stock" (ver Global Constraints).
    is_in_stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
```

- [ ] **Step 4: Crear la clase `StoreSetting`** en `src/skudo/mirror/models.py` (usa `Base`, `Mapped`, `mapped_column`, `String`, `Integer`, `DateTime`, `func`, `ForeignKey`, `UniqueConstraint` ya importados en el archivo):

```python
class StoreSetting(Base):
    """Configuración de una store view del Magento del tenant, clave/valor.

    Genérica a propósito: hoy guarda `show_out_of_stock`, y mañana otra config
    de tienda entra sin una migración nueva. El valor es texto ('true'/'false'
    para banderas) porque una config de Magento no tiene un tipo uniforme.
    """

    __tablename__ = "store_setting"
    __table_args__ = (
        UniqueConstraint("tenant_id", "store_view_magento_id", "key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 5: Añadir `prioridad_vitrina` a `ProductScore`** en `src/skudo/score/models.py`, tras `deducciones`:

```python
    # Prioridad de vitrina: 'pleno' (con stock) o 'sin_stock' (sin stock pero
    # visible). NULL si no se pudo determinar (sin datos de stock). No entra en
    # el puntaje; ordena el panel. Los ocultos por falta de stock ni siquiera
    # llegan acá: no son PUBLICADO, así que no se puntúan.
    prioridad_vitrina: Mapped[str | None] = mapped_column(String(16), nullable=True)
```

- [ ] **Step 6: Crear la migración** `alembic/versions/0021_stock_y_visibilidad.py`:

```python
"""stock y visibilidad

Revision ID: 0021
Revises: 0020
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_signal", sa.Column("is_in_stock", sa.Boolean(), nullable=True))
    op.add_column("product_score", sa.Column("prioridad_vitrina", sa.String(length=16), nullable=True))
    op.create_table(
        "store_setting",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False, index=True),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False, index=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "store_view_magento_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("store_setting")
    op.drop_column("product_score", "prioridad_vitrina")
    op.drop_column("product_signal", "is_in_stock")
```

- [ ] **Step 7: Verificar `alembic/env.py`** — `StoreSetting` vive en `skudo.mirror.models`, que ya se importa; confirmar que no hace falta un import nuevo. Si el proyecto importa modelos por módulo, no tocar.

- [ ] **Step 8: Correr los tests (con la migración aplicándose en el fixture)**

Run: `.venv/bin/python -m pytest tests/mirror/test_store_setting.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/skudo/mirror/models.py src/skudo/score/models.py alembic/versions/0021_stock_y_visibilidad.py tests/mirror/test_store_setting.py
git commit -m "feat(mirror): is_in_stock, store_setting y prioridad_vitrina (migración 0021)"
```

---

### Task 2: Accesor de config y mapeo de is_in_stock en señales

**Files:**
- Modify: `src/skudo/mirror/signals.py` (`_UPDATABLE` gana `is_in_stock`)
- Create: `src/skudo/mirror/store_settings.py` (accesor `muestra_sin_stock`)
- Test: `tests/mirror/test_muestra_sin_stock.py`

**Interfaces:**
- Consumes: `StoreSetting` (Task 1).
- Produces: `muestra_sin_stock(session, tenant_id, store_view_magento_id) -> bool | None` — `True`/`False` si la config `show_out_of_stock` existe, `None` si no está sincronizada. `upsert_signals` acepta `is_in_stock` en cada row.

- [ ] **Step 1: Test que falla** (`tests/mirror/test_muestra_sin_stock.py`)

```python
from skudo.mirror.models import StoreSetting, Tenant
from skudo.mirror.store_settings import muestra_sin_stock


def test_none_si_no_hay_config(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    assert muestra_sin_stock(db_session, t.id, 1) is None


def test_true_y_false_segun_valor(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                key="show_out_of_stock", value="true"))
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=2,
                                key="show_out_of_stock", value="false"))
    db_session.flush()
    assert muestra_sin_stock(db_session, t.id, 1) is True
    assert muestra_sin_stock(db_session, t.id, 2) is False
```

- [ ] **Step 2: Correr y ver que falla**

Run: `.venv/bin/python -m pytest tests/mirror/test_muestra_sin_stock.py -q`
Expected: FAIL (`ModuleNotFoundError: skudo.mirror.store_settings`).

- [ ] **Step 3: Crear el accesor** `src/skudo/mirror/store_settings.py`:

```python
"""Lectura de configuración de tienda del espejo."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import StoreSetting

_VERDADEROS = {"true", "1", "yes", "si"}
_FALSOS = {"false", "0", "no"}


def muestra_sin_stock(session: Session, tenant_id: int, store_view_magento_id: int) -> bool | None:
    """¿Magento muestra los productos sin stock en esta store view?

    `None` cuando la config no está sincronizada: es 'no sé', no 'no'. El motor
    lo trata como ignorancia y no oculta a nadie (ver Global Constraints).
    """
    valor = session.scalar(
        select(StoreSetting.value).where(
            StoreSetting.tenant_id == tenant_id,
            StoreSetting.store_view_magento_id == store_view_magento_id,
            StoreSetting.key == "show_out_of_stock",
        )
    )
    if valor is None:
        return None
    v = valor.strip().lower()
    if v in _VERDADEROS:
        return True
    if v in _FALSOS:
        return False
    return None
```

- [ ] **Step 4: Añadir `is_in_stock` a `_UPDATABLE`** en `src/skudo/mirror/signals.py`:

```python
_UPDATABLE = (
    "units_sold", "revenue", "salable_qty", "physical_qty",
    "uses_msi", "margin", "search_demand", "is_in_stock",
)
```

- [ ] **Step 5: Correr los tests**

Run: `.venv/bin/python -m pytest tests/mirror/test_muestra_sin_stock.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skudo/mirror/store_settings.py src/skudo/mirror/signals.py tests/mirror/test_muestra_sin_stock.py
git commit -m "feat(mirror): accesor muestra_sin_stock y mapeo de is_in_stock en señales"
```

---

### Task 3: Nivel de vitrina en la Ficha (estado y prioridad)

**Files:**
- Modify: `src/skudo/findings/catalog.py` (constante `OCULTO_SIN_STOCK`; campos `is_in_stock`, `muestra_sin_stock` en `Ficha`; refinamiento de `estado`; propiedad `prioridad_vitrina`)
- Test: `tests/findings/test_vitrina_stock.py`

**Interfaces:**
- Consumes: nada nuevo (lógica pura sobre campos de la Ficha).
- Produces: `Ficha` con `is_in_stock: bool | None = None`, `muestra_sin_stock: bool | None = None`; `Ficha.estado` puede devolver `OCULTO_SIN_STOCK`; `Ficha.prioridad_vitrina -> str | None` (`"pleno"`/`"sin_stock"`/`None`). `_publicado(f)` sigue `True` solo si `estado == PUBLICADO`.

- [ ] **Step 1: Test que falla** (`tests/findings/test_vitrina_stock.py`) — la tabla de verdad y el invariante de ignorancia:

```python
from skudo.findings.catalog import OCULTO_SIN_STOCK, PUBLICADO, Ficha, _publicado

BASE = {"status": "1", "visibility": "4"}  # publicado y navegable


def _f(**kw):
    stock = {k: kw.pop(k) for k in ("is_in_stock", "muestra_sin_stock") if k in kw}
    return Ficha(sku="X", attributes={**BASE}, **stock)


def test_con_stock_es_pleno():
    f = _f(is_in_stock=True, muestra_sin_stock=False)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "pleno"


def test_sin_stock_y_oculto_no_es_publicado():
    f = _f(is_in_stock=False, muestra_sin_stock=False)
    assert f.estado == OCULTO_SIN_STOCK
    assert _publicado(f) is False
    assert f.prioridad_vitrina is None


def test_sin_stock_pero_visible_es_publicado_con_prioridad_menor():
    f = _f(is_in_stock=False, muestra_sin_stock=True)
    assert f.estado == PUBLICADO
    assert _publicado(f) is True
    assert f.prioridad_vitrina == "sin_stock"


def test_ignorancia_stock_desconocido_se_comporta_como_hoy():
    # is_in_stock None: como si la feature no existiera -> publicado y pleno.
    f = _f(is_in_stock=None, muestra_sin_stock=False)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "pleno"


def test_ignorancia_config_desconocida_no_oculta():
    # sin stock explícito pero config desconocida: gana la ignorancia -> publicado.
    f = _f(is_in_stock=False, muestra_sin_stock=None)
    assert f.estado == PUBLICADO
    assert f.prioridad_vitrina == "pleno"


def test_no_navegable_no_se_toca_por_stock():
    f = Ficha(sku="X", attributes={"status": "1", "visibility": "1"},
              is_in_stock=False, muestra_sin_stock=False)
    assert f.estado != OCULTO_SIN_STOCK  # sigue siendo NO_NAVEGABLE
```

- [ ] **Step 2: Correr y ver que falla**

Run: `.venv/bin/python -m pytest tests/findings/test_vitrina_stock.py -q`
Expected: FAIL (`OCULTO_SIN_STOCK` no existe / `prioridad_vitrina` no existe).

- [ ] **Step 3: Añadir la constante** junto a `PUBLICADO`, etc. en `src/skudo/findings/catalog.py`:

```python
OCULTO_SIN_STOCK = "oculto_sin_stock"
```

- [ ] **Step 4: Añadir los campos a la `Ficha`** (dataclass frozen), tras `categorias`:

```python
    # Stock por producto y config por store view. Ambos None por defecto para
    # que un llamador que no los provea obtenga el comportamiento de siempre.
    is_in_stock: bool | None = None
    muestra_sin_stock: bool | None = None
```

- [ ] **Step 5: Refinar `estado`** — tras decidir que sería `PUBLICADO`, mirar el stock:

```python
    @property
    def estado(self) -> str:
        status = self.valor("status")
        visibility = self.valor("visibility")
        if status is None or visibility is None:
            return DESCONOCIDO
        if status != "1":
            return DESHABILITADO
        if visibility not in VISIBILIDADES_NAVEGABLES:
            return NO_NAVEGABLE
        # Publicado por status+visibility. Solo lo oculta un sin-stock EXPLÍCITO
        # con una config de ocultar EXPLÍCITA: cualquier None deja PUBLICADO.
        if self.is_in_stock is False and self.muestra_sin_stock is False:
            return OCULTO_SIN_STOCK
        return PUBLICADO

    @property
    def prioridad_vitrina(self) -> str | None:
        """Prioridad de un producto ya publicado: 'pleno' o 'sin_stock'. None si
        no es publicado (los ocultos no se listan)."""
        if self.estado != PUBLICADO:
            return None
        if self.is_in_stock is False and self.muestra_sin_stock is True:
            return "sin_stock"
        return "pleno"
```

- [ ] **Step 6: Correr los tests**

Run: `.venv/bin/python -m pytest tests/findings/test_vitrina_stock.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/skudo/findings/catalog.py tests/findings/test_vitrina_stock.py
git commit -m "feat(findings): nivel de vitrina por stock en la Ficha (oculto/sin_stock/pleno)"
```

---

### Task 4: Conectar stock a la construcción de fichas

**Files:**
- Modify: `src/skudo/findings/run.py` (`fichas_de` lee `is_in_stock` de `product_signal` y `muestra_sin_stock` de la config)
- Test: `tests/findings/test_run_stock.py`

**Interfaces:**
- Consumes: `ProductSignal.is_in_stock` (Task 1), `muestra_sin_stock` (Task 2), `Ficha` con stock (Task 3).
- Produces: `fichas_de` devuelve fichas con `is_in_stock` y `muestra_sin_stock` cargados; un `oculto_sin_stock` no produce hallazgos en `detect_store_view`.

- [ ] **Step 1: Test que falla** (`tests/findings/test_run_stock.py`)

```python
from skudo.findings.models import Finding
from skudo.findings.run import detect_store_view
from skudo.mirror.models import ProductRecord, ProductSignal, StoreSetting, Tenant


def _setup(session, *, is_in_stock, muestra):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t); session.flush()
    # producto publicado SIN descripción (generaría hallazgo) — el stock decide.
    session.add(ProductRecord(tenant_id=t.id, sku="1007", store_view_magento_id=1,
                              attributes={"status": "1", "visibility": "4"},
                              attribute_set_id=4, type_id="simple",
                              sync_generation=1, scope_provenance={}, content_hash="1007"))
    session.add(ProductSignal(tenant_id=t.id, sku="1007", store_view_magento_id=1,
                              uses_msi=False, is_in_stock=is_in_stock))
    if muestra is not None:
        session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                 key="show_out_of_stock", value="true" if muestra else "false"))
    session.flush()
    return t


def test_oculto_sin_stock_no_genera_hallazgos(db_session):
    t = _setup(db_session, is_in_stock=False, muestra=False)
    run = detect_store_view(db_session, t.id, 1)
    n = db_session.query(Finding).filter(Finding.run_id == run.id,
                                         Finding.subject_key == "1007").count()
    assert n == 0


def test_sin_stock_visible_si_genera_hallazgos(db_session):
    t = _setup(db_session, is_in_stock=False, muestra=True)
    run = detect_store_view(db_session, t.id, 1)
    n = db_session.query(Finding).filter(Finding.run_id == run.id,
                                         Finding.subject_key == "1007").count()
    assert n > 0


def test_sin_datos_de_stock_se_comporta_como_hoy(db_session):
    # is_in_stock None y sin config: el producto se evalúa como siempre.
    t = _setup(db_session, is_in_stock=None, muestra=None)
    run = detect_store_view(db_session, t.id, 1)
    n = db_session.query(Finding).filter(Finding.run_id == run.id,
                                         Finding.subject_key == "1007").count()
    assert n > 0
```

- [ ] **Step 2: Correr y ver que falla**

Run: `.venv/bin/python -m pytest tests/findings/test_run_stock.py -q`
Expected: FAIL en `test_oculto_sin_stock_no_genera_hallazgos` (hoy genera hallazgo: la ficha no conoce el stock).

- [ ] **Step 3: Cargar stock y config en `fichas_de`** (`src/skudo/findings/run.py`). Añadir el import y leer ambos:

```python
from skudo.mirror.models import ProductCategoryAssignment, ProductRecord, ProductSignal
from skudo.mirror.store_settings import muestra_sin_stock
```

Dentro de `fichas_de`, antes de armar las fichas, leer `is_in_stock` por sku y la config una vez:

```python
    stock_por_sku = dict(
        session.execute(
            select(ProductSignal.sku, ProductSignal.is_in_stock).where(
                ProductSignal.tenant_id == tenant_id,
                ProductSignal.store_view_magento_id == store_view_magento_id,
            )
        ).all()
    )
    muestra = muestra_sin_stock(session, tenant_id, store_view_magento_id)
```

Y pasarlos a cada `Ficha`:

```python
    return [
        Ficha(
            sku=sku,
            attributes=attrs or {},
            attribute_set_id=set_id,
            type_id=type_id,
            categorias=tuple(sorted(categorias.get(sku, ()))),
            is_in_stock=stock_por_sku.get(sku),
            muestra_sin_stock=muestra,
        )
        for sku, attrs, set_id, type_id in filas
    ]
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/python -m pytest tests/findings/test_run_stock.py -q`
Expected: PASS.

- [ ] **Step 5: Correr la suite de findings para no romper nada**

Run: `.venv/bin/python -m pytest tests/findings/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/skudo/findings/run.py tests/findings/test_run_stock.py
git commit -m "feat(findings): fichas_de carga is_in_stock y show_out_of_stock; los ocultos no penalizan"
```

---

### Task 5: Persistir la prioridad de vitrina en la nota del producto

**Files:**
- Modify: `src/skudo/score/run.py` (`score_run` escribe `prioridad_vitrina` por sku)
- Test: `tests/score/test_prioridad_vitrina.py`

**Interfaces:**
- Consumes: `Ficha.prioridad_vitrina` (Task 3), `ProductScore.prioridad_vitrina` (Task 1).
- Produces: cada `ProductScore` escrito lleva `prioridad_vitrina` del sku; los `oculto_sin_stock` no se puntúan (ya no son `PUBLICADO`).

- [ ] **Step 1: Test que falla** (`tests/score/test_prioridad_vitrina.py`)

```python
from skudo.findings.run import detect_store_view
from skudo.mirror.models import ProductRecord, ProductSignal, StoreSetting, Tenant
from skudo.score.models import ProductScore
from skudo.score.run import score_run


def _prod(session, t, sku, is_in_stock):
    session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                              attributes={"status": "1", "visibility": "4", "color": "rojo"},
                              attribute_set_id=4, type_id="simple",
                              sync_generation=1, scope_provenance={}, content_hash=sku))
    session.add(ProductSignal(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                              uses_msi=False, is_in_stock=is_in_stock))


def test_prioridad_se_persiste_y_oculto_no_se_puntua(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    db_session.add(StoreSetting(tenant_id=t.id, store_view_magento_id=1,
                                key="show_out_of_stock", value="false"))  # oculta sin stock
    _prod(db_session, t, "con", is_in_stock=True)     # pleno
    _prod(db_session, t, "vis", is_in_stock=None)     # desconocido -> pleno (ignorancia)
    _prod(db_session, t, "ocu", is_in_stock=False)    # oculto: no debe puntuarse
    db_session.flush()

    run = detect_store_view(db_session, t.id, 1)
    score_run(db_session, run)

    por_sku = {p.sku: p for p in db_session.query(ProductScore).filter_by(
        tenant_id=t.id, store_view_magento_id=1)}
    assert por_sku["con"].prioridad_vitrina == "pleno"
    assert por_sku["vis"].prioridad_vitrina == "pleno"
    assert "ocu" not in por_sku  # oculto por falta de stock: no se puntúa
```

- [ ] **Step 2: Correr y ver que falla**

Run: `.venv/bin/python -m pytest tests/score/test_prioridad_vitrina.py -q`
Expected: FAIL (`prioridad_vitrina` no se escribe / la columna llega None).

- [ ] **Step 3: Escribir `prioridad_vitrina` en `score_run`** (`src/skudo/score/run.py`). Construir el mapa desde las fichas ya calculadas y sumarlo a cada fila y al `on_conflict_do_update`:

```python
    fichas = fichas_de(session, run.tenant_id, run.store_view_magento_id)
    prioridad_por_sku = {f.sku: f.prioridad_vitrina for f in fichas}
    publicados = [f.sku for f in fichas if f.estado == PUBLICADO]
    ...
    filas = [
        {
            "tenant_id": run.tenant_id,
            "sku": n.sku,
            "store_view_magento_id": run.store_view_magento_id,
            "puntaje": n.puntaje,
            "grado": n.grado,
            "critico": n.critico,
            "deducciones": n.deducciones,
            "prioridad_vitrina": prioridad_por_sku.get(n.sku),
            "run_id": run.id,
        }
        for n in notas
    ]
```

Y en el `set_=` del `on_conflict_do_update`, añadir:

```python
                    "prioridad_vitrina": stmt.excluded.prioridad_vitrina,
```

- [ ] **Step 4: Correr los tests**

Run: `.venv/bin/python -m pytest tests/score/test_prioridad_vitrina.py tests/score/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/skudo/score/run.py tests/score/test_prioridad_vitrina.py
git commit -m "feat(score): persistir prioridad_vitrina; los ocultos por stock no se puntúan"
```

---

### Task 6: Superficie en el panel (endpoint + vista Productos)

**Files:**
- Modify: `src/skudo/web/app.py` (`tenant_products` devuelve `prioridad_vitrina` y ordena por prioridad)
- Modify: `src/skudo/web/static/index.html` (badge de vitrina, orden, nota de ocultos)
- Test: `tests/web/test_products_prioridad.py`

**Interfaces:**
- Consumes: `ProductScore.prioridad_vitrina` (Task 1/5).
- Produces: cada producto del endpoint incluye `"prioridad_vitrina"`; el listado ordena `pleno` antes que `sin_stock`; el panel muestra el estado de vitrina y explica los ocultos.

- [ ] **Step 1: Test que falla** (`tests/web/test_products_prioridad.py`)

```python
from fastapi.testclient import TestClient

from skudo.mirror.models import Tenant
from skudo.score.models import ProductScore
from skudo.web.app import app, get_db
from skudo.web.auth import create_token


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'a@x.com', 'administrador')}"}


def test_products_devuelve_y_ordena_por_prioridad(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    for sku, prio, puntaje in [("sinstock", "sin_stock", 90), ("pleno", "pleno", 90)]:
        db_session.add(ProductScore(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                    puntaje=puntaje, grado="A", critico=False,
                                    deducciones=[], prioridad_vitrina=prio, run_id=1))
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/products?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    prods = resp.json()["productos"]
    assert prods[0]["prioridad_vitrina"] == "pleno"       # pleno primero
    assert {p["prioridad_vitrina"] for p in prods} == {"pleno", "sin_stock"}
```

- [ ] **Step 2: Correr y ver que falla**

Run: `.venv/bin/python -m pytest tests/web/test_products_prioridad.py -q`
Expected: FAIL (falta la clave `prioridad_vitrina` y el orden).

- [ ] **Step 3: Ordenar y devolver la prioridad en `tenant_products`** (`src/skudo/web/app.py`). Cambiar el `order_by` para que `pleno` anteceda a `sin_stock` (los `pleno` tienen prioridad; `NULLS` al final), manteniendo el orden por peor nota dentro de cada grupo:

```python
    from sqlalchemy import case
    orden_prioridad = case({"pleno": 0, "sin_stock": 1}, value=ProductScore.prioridad_vitrina, else_=2)
    filas = db.scalars(
        q.order_by(orden_prioridad, ProductScore.puntaje, ProductScore.sku)
        .limit(min(limit, 200))
        .offset(offset)
    ).all()
```

Y añadir la clave al dict de cada producto:

```python
                "prioridad_vitrina": p.prioridad_vitrina,
```

- [ ] **Step 4: Correr el test del endpoint**

Run: `.venv/bin/python -m pytest tests/web/test_products_prioridad.py -q`
Expected: PASS.

- [ ] **Step 5: Panel — badge y explicación** (`src/skudo/web/static/index.html`). En `renderProducts()`, para cada producto, mostrar un badge según `prioridad_vitrina`:
  - `pleno` → sin badge (es lo normal), o un badge sutil "Con stock".
  - `sin_stock` → badge "Sin stock · visible".
  - `null` → badge "Stock desconocido".

Añadir un diccionario y usarlo en la fila:

```javascript
const VITRINA = {
  pleno: { label: 'Con stock', clase: 'vitrina-pleno' },
  sin_stock: { label: 'Sin stock · visible', clase: 'vitrina-sinstock' },
};
function vitrinaBadge(p) {
  const info = VITRINA[p.prioridad_vitrina];
  if (!info) return el('span', { class: 'severity-badge vitrina-desconocido', title: 'No hay datos de stock sincronizados para este producto.' }, 'Stock desconocido');
  return el('span', { class: `severity-badge ${info.clase}`, title: p.prioridad_vitrina === 'sin_stock' ? 'Sin stock, pero Magento lo muestra: cuenta con menor prioridad.' : 'Con stock: prioridad plena.' }, info.label);
}
```

Insertar el badge en la fila del producto (en la celda de sku o una columna nueva "Vitrina"), y añadir una nota bajo el título de la vista:

```javascript
el('p', { class: 'section-intro' },
  'Los productos sin stock que Magento oculta no aparecen acá ni penalizan la nota. Los que están sin stock pero visibles se listan al final, con menor prioridad.'),
```

- [ ] **Step 6: CSS de los badges** (junto a los otros `severity-badge` en `<style>`):

```css
.vitrina-pleno { background: var(--positive-light); color: var(--positive); }
.vitrina-sinstock { background: var(--warning-light); color: var(--warning); }
.vitrina-desconocido { background: var(--surface); color: var(--ink-secondary); border: 1px solid var(--border); }
```

- [ ] **Step 7: Verificar sintaxis del panel y correr los tests web**

Run: `node --check` sobre el `<script>` extraído (o revisión visual), y
`.venv/bin/python -m pytest tests/web/ -q`
Expected: JS válido; tests web PASS.

- [ ] **Step 8: Commit**

```bash
git add src/skudo/web/app.py src/skudo/web/static/index.html tests/web/test_products_prioridad.py
git commit -m "feat(web): estado de vitrina en Productos (badge, orden por prioridad, nota de ocultos)"
```

---

## Fuera de este plan (plan posterior, bloqueado por red)

El módulo PHP y la sincronización real dependen de que el server alcance el Magento (443, pendiente de TI). Un plan aparte cubrirá:
- `SignalReader::attachInventory()` trae `is_in_stock` de `cataloginventory_stock_item`.
- Lectura de `cataloginventory/options/show_out_of_stock` por store view (EnvironmentProbe o `/store-settings`).
- Syncs: `signal_sync` ya mapeará `is_in_stock` (Task 2); un sync de config puebla `store_setting`.

Hasta entonces, `is_in_stock`/`show_out_of_stock` quedan en NULL y —por la regla de ignorancia— la nota se comporta exactamente como hoy.
