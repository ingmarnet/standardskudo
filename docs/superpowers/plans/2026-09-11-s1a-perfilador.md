# S1a — Perfilador: plan de implementación

> **Para trabajadores agénticos:** SUB-SKILL REQUERIDA: usar
> superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para
> implementar este plan tarea por tarea. Los pasos usan casillas (`- [ ]`).

**Objetivo:** medir el catálogo espejado —cobertura por los cuatro estados, particiones
descubiertas, distribuciones y poder discriminante— por `(partición, store view)`, de forma
reproducible, para que S1b pueda inferir reglas con evidencia en vez de con suposiciones.

**Arquitectura:** un paquete `skudo.profile` de funciones puras sobre filas del espejo, más
una pasada que las orquesta y sella su resultado. No habla con Magento, no escribe en el
espejo, no llama a la IA. Lee `product_record`, `attribute`, `product_category_assignment`
y `category`, y escribe cuatro tablas nuevas de perfil.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 tipado, Alembic, Postgres 16, pytest, ruff.

**Estado (2026-09-11):** **Tasks 1 a 8 ejecutadas y en verde** — 398 tests, ruff limpio,
migración 0015 de ida y vuelta, y el comando corriendo contra un espejo real. La ejecución
encontró un defecto que el plan no traía (dos definiciones de "presente"; ver el commit
`fix(s1a)`). **Task 9 pendiente**: necesita autorización para leer el catálogo de producción.

**Spec:** `docs/superpowers/specs/2026-09-11-s1-perfilador-design.md`, que a su vez depende
de `docs/superpowers/specs/2026-09-08-standardskudo-catalog-quality-design.md` (revisión 3).

## Restricciones globales

- **Nada se escribe en el Magento de producción.** Este sub-proyecto no habla con Magento
  en absoluto: lee el espejo Postgres. Cualquier tarea que crea necesitar Magento está mal
  entendida.
- **Los cuatro estados nunca se colapsan:** `presente`, `vacio`, `no_aplica`, `desconocido`.
  `"0"` es **presente**. Clave ausente en un atributo del set es **vacío**, no `no_aplica`.
- **`desconocido` no cuenta como cobertura ni como carencia.** Una cobertura sin
  denominador es `None`, nunca `0.0`.
- **Los umbrales son constantes del código con su justificación al lado**, nunca
  configuración por tenant ni argumentos de CLI. Cambiarlos es editar código y que se vea
  en el diff.
- **Determinismo:** dos pasadas sobre el mismo espejo producen el mismo digest. Todo
  recorrido de diccionarios va ordenado; todo empate se rompe por orden alfabético.
- **El perfil es por `(partición, store view)`.** Nunca se promedian store views.
- `ruff` con `line-length = 100`; docstrings en español explicando **por qué**, no qué.
- Los tests corren contra el Postgres real de `docker-compose.yml` (`skudo_test`), con los
  fixtures existentes `migrated_engine` y `db_session` de `tests/conftest.py`.
- Para sembrar filas en los tests se usa `tests/skudo_testing.py` (`upsert_record`,
  `set_product_categories`), que delega en el camino de escritura del producto.
- Toda función de escritura nueva bajo `src/skudo/` necesita un llamador que no sea un
  test: `tests/mirror/test_write_paths_are_reachable.py` lo comprueba y la Task 1 amplía su
  alcance a `src/skudo/profile/`.

---

## Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `src/skudo/profile/models.py` | Las cuatro tablas de perfil. Importa `Base` de `skudo.mirror.models`. |
| `src/skudo/profile/states.py` | Los cuatro estados de un atributo en un producto. Función pura. |
| `src/skudo/profile/coverage.py` | Vector de cobertura de un conjunto de productos. Función pura. |
| `src/skudo/profile/partition.py` | Ambigüedad, candidatos a divisor, elección con umbrales. Funciones puras. |
| `src/skudo/profile/distribution.py` | Parseo numérico, percentiles, cardinalidad, poder discriminante. Puras. |
| `src/skudo/profile/run.py` | La pasada: lee el espejo, orquesta, escribe y sella. |
| `src/skudo/profile/report.py` | El informe legible de una pasada. |
| `alembic/versions/0015_profile_tables.py` | La migración. |
| `src/skudo/cli.py` | El comando `profile` (modificado). |

---

### Task 1: Las tablas de perfil

**Files:**
- Create: `src/skudo/profile/__init__.py`, `src/skudo/profile/models.py`
- Create: `alembic/versions/0015_profile_tables.py`
- Modify: `tests/mirror/test_write_paths_are_reachable.py` (ampliar alcance a `profile/`)
- Test: `tests/profile/test_models.py`

**Interfaces:**
- Consumes: `skudo.mirror.models.Base`, `Tenant`.
- Produces: `ProfileRun`, `ProfilePartition`, `AttributeCoverage`, `ValueStats`, con los
  nombres de columna que usan todas las tareas siguientes.

- [ ] **Step 1: Escribir el test que falla**

`tests/profile/test_models.py` (sin `__init__.py`: `tests/` no es un paquete, y los ayudantes compartidos viven en `tests/skudo_testing.py`, que ya se importa así):

```python
import pytest
from sqlalchemy.exc import IntegrityError

from skudo.mirror.models import Tenant
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun


def _tenant(session) -> Tenant:
    tenant = Tenant(
        code="acme", name="Acme", base_url="http://acme.test", token_env_var="X"
    )
    session.add(tenant)
    session.flush()
    return tenant


def test_una_pasada_guarda_sus_umbrales_y_su_generacion(db_session):
    tenant = _tenant(db_session)
    run = ProfileRun(
        tenant_id=tenant.id,
        store_view_magento_id=1,
        mirror_sync_generation=7,
        thresholds={"MIN_PARTICION": 50},
        product_count=0,
    )
    db_session.add(run)
    db_session.flush()

    assert run.digest is None, "una pasada sin terminar no tiene sello"
    assert run.finished_at is None
    assert run.thresholds["MIN_PARTICION"] == 50


def test_la_cobertura_es_nula_cuando_no_hay_denominador(db_session):
    """`None` y `0.0` son cosas distintas y la columna tiene que poder decirlo."""
    tenant = _tenant(db_session)
    run = ProfileRun(
        tenant_id=tenant.id,
        store_view_magento_id=1,
        mirror_sync_generation=1,
        thresholds={},
        product_count=0,
    )
    db_session.add(run)
    db_session.flush()
    particion = ProfilePartition(
        run_id=run.id,
        attribute_set_id=4,
        splitter_kind="ninguno",
        splitter_value=None,
        product_count=3,
        ambiguity=None,
        decision_reason="sin candidatos",
    )
    db_session.add(particion)
    db_session.flush()
    fila = AttributeCoverage(
        partition_id=particion.id,
        attribute_code="color",
        presente=0,
        vacio=0,
        no_aplica=0,
        desconocido=3,
        coverage=None,
    )
    db_session.add(fila)
    db_session.flush()

    assert fila.coverage is None


def test_un_atributo_no_puede_repetirse_en_la_misma_particion(db_session):
    tenant = _tenant(db_session)
    run = ProfileRun(
        tenant_id=tenant.id,
        store_view_magento_id=1,
        mirror_sync_generation=1,
        thresholds={},
        product_count=0,
    )
    db_session.add(run)
    db_session.flush()
    particion = ProfilePartition(
        run_id=run.id,
        attribute_set_id=4,
        splitter_kind="ninguno",
        splitter_value=None,
        product_count=1,
        ambiguity=None,
        decision_reason="sin candidatos",
    )
    db_session.add(particion)
    db_session.flush()
    for _ in range(2):
        db_session.add(
            AttributeCoverage(
                partition_id=particion.id,
                attribute_code="color",
                presente=1,
                vacio=0,
                no_aplica=0,
                desconocido=0,
                coverage=1.0,
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/profile/test_models.py -x`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.profile'`.

- [ ] **Step 3: Escribir los modelos**

`src/skudo/profile/__init__.py` vacío. `src/skudo/profile/models.py`:

```python
"""Las tablas del perfil.

Un perfil NO es parte del espejo: el espejo dice qué hay en Magento y el perfil
dice qué se midió sobre eso. Viven en módulos distintos para que una migración
del perfil no pueda tocar por accidente la fidelidad del espejo, que es lo único
que no se puede recalcular sin volver a hablar con el cliente.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class ProfileRun(Base):
    """Una pasada del perfilador sobre una store view.

    `mirror_sync_generation` y `thresholds` viajan con la fila porque un perfil
    sin ellos es indiscutible: cuando alguien pregunte por qué una regla salió
    así, la respuesta tiene que estar en la propia fila y no en la memoria de
    quien lanzó el comando.
    """

    __tablename__ = "profile_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer, index=True)
    mirror_sync_generation: Mapped[int] = mapped_column(BigInteger)
    thresholds: Mapped[dict] = mapped_column(JSON)
    product_count: Mapped[int] = mapped_column(Integer)
    # NULL mientras la pasada no termina. Un digest a medias es peor que ninguno:
    # invita a comparar dos perfiles que no midieron lo mismo.
    digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProfilePartition(Base):
    """Un grupo de productos sobre el que se mide.

    `decision_reason` guarda POR QUÉ esta partición es como es —"sin candidatos",
    "ganancia insuficiente", "grupo pequeño", "elegido"—. Que el perfilador diga
    cuándo NO encontró subtipo vale tanto como cuando lo encuentra: sin eso, un
    set homogéneo y un set que nadie supo dividir son indistinguibles.
    """

    __tablename__ = "profile_partition"
    __table_args__ = (
        UniqueConstraint("run_id", "attribute_set_id", "splitter_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("profile_run.id"), index=True)
    # NULL = la partición de los productos cuyo attribute set el espejo desconoce.
    attribute_set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    splitter_kind: Mapped[str] = mapped_column(String(32))
    splitter_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    splitter_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_count: Mapped[int] = mapped_column(Integer)
    ambiguity: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_reason: Mapped[str] = mapped_column(String(64))


class AttributeCoverage(Base):
    """Los cuatro estados de un atributo dentro de una partición.

    Las cuatro columnas son cuatro, y no un total con porcentaje, porque el spec
    prohíbe colapsarlas: un atributo con 900 vacíos y otro con 900 desconocidos
    tienen la misma cobertura y significan cosas opuestas.
    """

    __tablename__ = "profile_attribute_coverage"
    __table_args__ = (UniqueConstraint("partition_id", "attribute_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    partition_id: Mapped[int] = mapped_column(
        ForeignKey("profile_partition.id"), index=True
    )
    attribute_code: Mapped[str] = mapped_column(String(255))
    presente: Mapped[int] = mapped_column(Integer)
    vacio: Mapped[int] = mapped_column(Integer)
    no_aplica: Mapped[int] = mapped_column(Integer)
    desconocido: Mapped[int] = mapped_column(Integer)
    # NULL cuando no hay denominador. NO es 0.0: "no se pudo medir" y "no lo
    # tiene nadie" son el par de estados que este producto existe para no
    # confundir.
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)


class ValueStats(Base):
    """Qué valores toma un atributo dentro de una partición."""

    __tablename__ = "profile_value_stats"
    __table_args__ = (UniqueConstraint("partition_id", "attribute_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    partition_id: Mapped[int] = mapped_column(
        ForeignKey("profile_partition.id"), index=True
    )
    attribute_code: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))
    n_present: Mapped[int] = mapped_column(Integer)
    # Valores que NO se pudieron leer como número sin adivinar. Se cuentan
    # aparte en vez de descartarse: son el insumo del detector de sospecha de
    # conversión de S1c.
    n_ambiguous: Mapped[int] = mapped_column(Integer, default=0)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    p05: Mapped[float | None] = mapped_column(Float, nullable=True)
    p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    distinct_values: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    discriminating_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_values: Mapped[list] = mapped_column(JSON, default=list)
```

- [ ] **Step 4: Escribir la migración**

Generar con `uv run alembic revision -m "profile tables"` y **reemplazar** el cuerpo (no
usar `--autogenerate`: el plan fija los nombres). Archivo
`alembic/versions/0015_profile_tables.py`, con `down_revision = "0014"` y `revision =
"0015"`; el `upgrade()` crea las cuatro tablas con las mismas columnas, índices y
`UniqueConstraint` que los modelos, y el `downgrade()` las borra en orden inverso.

- [ ] **Step 5: Ampliar el control permanente de caminos de escritura**

En `tests/mirror/test_write_paths_are_reachable.py`, el directorio que se escanea pasa de
`src/skudo/mirror/` a `src/skudo/mirror/` **y** `src/skudo/profile/`. El auto-test que
demuestra que el control no es vacuo se conserva tal cual.

- [ ] **Step 6: Verificar**

Run: `uv run pytest tests/profile tests/mirror -q && uv run ruff check src tests`
Expected: PASS. Además `uv run alembic downgrade -1 && uv run alembic upgrade head` sin
error contra `skudo_test`.

- [ ] **Step 7: Commit**

```bash
git add src/skudo/profile alembic/versions/0015_profile_tables.py tests/profile tests/mirror
git commit -m "feat(s1a): las cuatro tablas del perfil, con la cobertura nullable"
```

---

### Task 2: Los cuatro estados

**Files:**
- Create: `src/skudo/profile/states.py`
- Test: `tests/profile/test_states.py`

**Interfaces:**
- Produces: `State` (enum de `str`), `attribute_state(attributes, attribute_set_id, code,
  sets_by_code) -> State`, `sets_by_code(session, tenant_id) -> dict[str, frozenset[int]]`,
  `codes_by_set(session, tenant_id) -> dict[int, tuple[str, ...]]`.

- [ ] **Step 1: Escribir el test que falla**

`tests/profile/test_states.py`:

```python
from skudo.profile.states import State, attribute_state

SETS = {"color": frozenset({4, 9}), "peso": frozenset({4}), "voltaje": frozenset({9})}


def estado(attributes, set_id, code):
    return attribute_state(attributes, set_id, code, SETS)


def test_un_valor_normal_es_presente():
    assert estado({"color": "negro"}, 4, "color") is State.PRESENTE


def test_el_cero_es_un_valor_presente():
    """La trampa que más datos legítimos destruye: evaluar la verdad de la
    cadena en vez de su presencia. Un peso declarado como 0 es una afirmación."""
    assert estado({"peso": "0"}, 4, "peso") is State.PRESENTE
    assert estado({"peso": "0.0"}, 4, "peso") is State.PRESENTE
    assert estado({"peso": 0}, 4, "peso") is State.PRESENTE


def test_la_clave_ausente_en_un_atributo_del_set_es_vacio():
    """El módulo devuelve lo que existe en las tablas EAV. Un atributo del set
    sin fila es la carencia que el eje 3 existe para encontrar; leerlo como
    'no aplica' apagaría el eje entero."""
    assert estado({}, 4, "color") is State.VACIO


def test_la_cadena_vacia_y_los_espacios_son_vacio():
    assert estado({"color": ""}, 4, "color") is State.VACIO
    assert estado({"color": "   "}, 4, "color") is State.VACIO
    assert estado({"color": []}, 4, "color") is State.VACIO


def test_un_atributo_fuera_del_set_del_producto_no_aplica():
    assert estado({}, 4, "voltaje") is State.NO_APLICA


def test_sin_attribute_set_conocido_todo_es_desconocido():
    """`attribute_set_id` NULL significa que la sonda no lo informó. Sin él no
    se puede distinguir vacío de no_aplica, y fingir cualquiera de los dos es
    inventar un hecho."""
    assert estado({"color": "negro"}, None, "color") is State.DESCONOCIDO
    assert estado({}, None, "color") is State.DESCONOCIDO


def test_un_atributo_que_el_espejo_no_conoce_es_desconocido():
    assert estado({"raro": "x"}, 4, "raro") is State.DESCONOCIDO


def test_los_cuatro_estados_son_cuatro_valores_distintos():
    assert len({s.value for s in State}) == 4
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/profile/test_states.py -x`
Expected: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`src/skudo/profile/states.py`:

```python
"""Los cuatro estados de un dato, derivados del espejo.

El spec los declara innegociables: `presente`, `vacio`, `no_aplica`,
`desconocido`. Colapsar dos de ellos es el fallo que el principio rector del
proyecto nombra como el peor posible, y las dos formas de colapsarlos son
concretas y están probadas aquí: tratar `"0"` como ausencia, y tratar una clave
ausente como "no aplica".
"""

from collections import defaultdict
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute


class State(str, Enum):
    PRESENTE = "presente"
    VACIO = "vacio"
    NO_APLICA = "no_aplica"
    DESCONOCIDO = "desconocido"


def attribute_state(
    attributes: dict,
    attribute_set_id: int | None,
    code: str,
    sets_by_code: dict[str, frozenset[int]],
) -> State:
    """El estado de UN atributo en UN producto.

    El orden de las comprobaciones no es casual: la ignorancia gana a todo lo
    demás. Si no sabemos a qué set pertenece el producto, o no conocemos el
    atributo, ningún valor observado autoriza a concluir nada.
    """
    declared = sets_by_code.get(code)
    if declared is None or attribute_set_id is None:
        return State.DESCONOCIDO
    if attribute_set_id not in declared:
        return State.NO_APLICA
    if code not in attributes:
        return State.VACIO
    value = attributes[code]
    if value is None:
        return State.VACIO
    if isinstance(value, str) and value.strip() == "":
        return State.VACIO
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return State.VACIO
    return State.PRESENTE


def sets_by_code(session: Session, tenant_id: int) -> dict[str, frozenset[int]]:
    """A qué attribute sets pertenece cada atributo, según el espejo."""
    filas = session.execute(
        select(Attribute.code, Attribute.attribute_set_ids).where(
            Attribute.tenant_id == tenant_id
        )
    ).all()
    return {code: frozenset(ids or []) for code, ids in filas}


def codes_by_set(session: Session, tenant_id: int) -> dict[int, tuple[str, ...]]:
    """El índice invertido, ordenado.

    El perfilador recorre los ~109 atributos del set de cada producto, no los
    1.066 del catálogo: la diferencia entre 25 y 244 millones de evaluaciones.
    Las tuplas van ordenadas porque el determinismo del perfil depende de que
    el recorrido no dependa del orden de inserción.
    """
    acumulado: dict[int, list[str]] = defaultdict(list)
    for code, ids in sorted(sets_by_code(session, tenant_id).items()):
        for set_id in sorted(ids):
            acumulado[set_id].append(code)
    return {set_id: tuple(codes) for set_id, codes in sorted(acumulado.items())}
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/profile/test_states.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Test de los dos lectores del espejo**

Añadir a `tests/profile/test_states.py`, que necesita base de datos:

```python
from skudo.mirror.attributes import upsert_attribute
from skudo.mirror.models import Tenant
from skudo.profile.states import codes_by_set, sets_by_code


def test_los_indices_salen_del_espejo_y_van_ordenados(db_session):
    tenant = Tenant(code="acme", name="A", base_url="http://a.test", token_env_var="X")
    db_session.add(tenant)
    db_session.flush()
    for code, ids in [("peso", [4]), ("color", [9, 4]), ("voltaje", [9])]:
        upsert_attribute(
            db_session,
            tenant.id,
            {
                "code": code,
                "label": code,
                "frontend_input": "text",
                "declared_scope": "global",
                "is_filterable": False,
                "is_required": False,
                "attribute_set_ids": ids,
            },
            sync_generation=1,
        )

    assert sets_by_code(db_session, tenant.id)["color"] == frozenset({4, 9})
    assert codes_by_set(db_session, tenant.id) == {
        4: ("color", "peso"),
        9: ("color", "voltaje"),
    }
```

- [ ] **Step 6: Verificar y commit**

Run: `uv run pytest tests/profile -q && uv run ruff check src tests`

```bash
git add src/skudo/profile/states.py tests/profile/test_states.py
git commit -m "feat(s1a): los cuatro estados, con el cero como valor presente"
```

---

### Task 3: El vector de cobertura

**Files:**
- Create: `src/skudo/profile/coverage.py`
- Test: `tests/profile/test_coverage.py`

**Interfaces:**
- Consumes: `State`, `attribute_state` de la Task 2.
- Produces: `Counts` (dataclass congelada con `presente`, `vacio`, `no_aplica`,
  `desconocido`, y las propiedades `evaluables` y `cobertura`), y
  `coverage_vector(products, codes, sets_by_code) -> dict[str, Counts]`, donde `products`
  es un iterable de `ProductoPerfilado(sku, attributes, attribute_set_id, categorias)`.

- [ ] **Step 1: Escribir el test que falla**

`tests/profile/test_coverage.py`:

```python
import pytest

from skudo.profile.coverage import Counts, ProductoPerfilado, coverage_vector

SETS = {"color": frozenset({4}), "peso": frozenset({4}), "voltaje": frozenset({9})}
CODES = ("color", "peso")


def producto(attrs, set_id=4):
    return ProductoPerfilado(
        sku=f"sku{id(attrs)}", attributes=attrs, attribute_set_id=set_id, categorias=()
    )


def test_cuenta_presentes_y_vacios():
    vector = coverage_vector(
        [producto({"color": "negro"}), producto({}), producto({"color": "rojo"})],
        CODES,
        SETS,
    )
    assert vector["color"] == Counts(presente=2, vacio=1)
    assert vector["color"].cobertura == pytest.approx(2 / 3)


def test_sin_denominador_la_cobertura_es_none_y_no_cero():
    vector = coverage_vector([producto({"color": "negro"}, set_id=None)], CODES, SETS)
    assert vector["color"] == Counts(desconocido=1)
    assert vector["color"].cobertura is None


def test_cero_por_ciento_y_no_medible_no_son_lo_mismo():
    nadie = coverage_vector([producto({}), producto({})], CODES, SETS)["color"]
    nada = coverage_vector([producto({}, set_id=None)], CODES, SETS)["color"]
    assert nadie.cobertura == 0.0
    assert nada.cobertura is None


def test_dentro_de_una_particion_nadie_cae_en_no_aplica():
    """Invariante de construcción: una partición agrupa productos del MISMO set,
    y el vector solo recorre los atributos de ese set. Si aparece un `no_aplica`
    es que alguien mezcló sets en una partición."""
    vector = coverage_vector([producto({"color": "x"}), producto({})], CODES, SETS)
    assert all(c.no_aplica == 0 for c in vector.values())


def test_el_vector_solo_tiene_los_atributos_pedidos():
    vector = coverage_vector([producto({"voltaje": "220"})], CODES, SETS)
    assert sorted(vector) == ["color", "peso"]


def test_el_vector_esta_ordenado():
    vector = coverage_vector([producto({})], ("peso", "color"), SETS)
    assert list(vector) == ["color", "peso"]
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/profile/test_coverage.py -x` → `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`src/skudo/profile/coverage.py`:

```python
"""El vector de cobertura de un grupo de productos.

La cobertura de un atributo es `presente / (presente + vacio)`. Lo que NO entra
en el denominador es tan importante como lo que entra: un producto cuyo set se
desconoce no dice nada sobre si ese atributo debería estar, y meterlo en el
denominador convierte ignorancia en carencia.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from skudo.profile.states import State, attribute_state


@dataclass(frozen=True)
class ProductoPerfilado:
    """Lo mínimo que el perfilador necesita de un producto del espejo."""

    sku: str
    attributes: dict
    attribute_set_id: int | None
    categorias: tuple[int, ...]


@dataclass(frozen=True)
class Counts:
    presente: int = 0
    vacio: int = 0
    no_aplica: int = 0
    desconocido: int = 0

    @property
    def evaluables(self) -> int:
        return self.presente + self.vacio

    @property
    def cobertura(self) -> float | None:
        """`None` cuando no hay denominador, jamás `0.0`.

        Devolver cero aquí sería afirmar "ningún producto lo tiene" cuando lo
        cierto es "no se pudo mirar". Toda la aritmética de S1a propaga ese
        `None` en vez de sustituirlo.
        """
        if self.evaluables == 0:
            return None
        return self.presente / self.evaluables


def coverage_vector(
    products: Iterable[ProductoPerfilado],
    codes: Sequence[str],
    sets_by_code: dict[str, frozenset[int]],
) -> dict[str, Counts]:
    """Cuenta los cuatro estados de cada atributo sobre un grupo de productos."""
    conteos: dict[str, Counter] = {code: Counter() for code in sorted(codes)}
    for product in products:
        for code in conteos:
            estado = attribute_state(
                product.attributes, product.attribute_set_id, code, sets_by_code
            )
            conteos[code][estado] += 1
    return {
        code: Counts(
            presente=c[State.PRESENTE],
            vacio=c[State.VACIO],
            no_aplica=c[State.NO_APLICA],
            desconocido=c[State.DESCONOCIDO],
        )
        for code, c in conteos.items()
    }
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/profile/test_coverage.py -q` → PASS (6 tests).

- [ ] **Step 5: Sabotaje**

Cambiar `cobertura` para que devuelva `0.0` cuando no hay evaluables y comprobar que
fallan `test_sin_denominador_la_cobertura_es_none_y_no_cero` y
`test_cero_por_ciento_y_no_medible_no_son_lo_mismo`. Revertir. Si no falla ninguno, los
tests son huecos y hay que arreglarlos antes de seguir.

- [ ] **Step 6: Commit**

```bash
git add src/skudo/profile/coverage.py tests/profile/test_coverage.py
git commit -m "feat(s1a): vector de cobertura, con el denominador que excluye la ignorancia"
```

---

### Task 4: Ambigüedad y ganancia

**Files:**
- Create: `src/skudo/profile/partition.py`
- Test: `tests/profile/test_ambiguity.py`

**Interfaces:**
- Consumes: `Counts` de la Task 3.
- Produces: las constantes `MIN_PARTICION`, `MAX_CARD`, `MIN_GANANCIA`; y
  `ambiguity(vector) -> float | None`, `weighted_ambiguity(grupos) -> float | None`,
  `gain(antes, despues) -> float | None`.

- [ ] **Step 1: Escribir el test que falla**

`tests/profile/test_ambiguity.py`:

```python
import pytest

from skudo.profile.coverage import Counts
from skudo.profile.partition import ambiguity, gain, weighted_ambiguity


def vector(**coberturas):
    """Un vector con la cobertura pedida: `color=0.5` son 1 presente y 1 vacío."""
    salida = {}
    for code, c in coberturas.items():
        if c is None:
            salida[code] = Counts(desconocido=10)
        else:
            salida[code] = Counts(presente=int(c * 100), vacio=int((1 - c) * 100))
    return salida


def test_un_atributo_que_tienen_todos_no_es_ambiguo():
    assert ambiguity(vector(color=1.0)) == 0.0


def test_un_atributo_que_no_tiene_nadie_tampoco_es_ambiguo():
    """Cero por ciento es una afirmación tan útil como cien: dice que ese
    atributo no pertenece a la ficha de este grupo."""
    assert ambiguity(vector(color=0.0)) == 0.0


def test_la_mitad_es_el_maximo_de_ambiguedad():
    assert ambiguity(vector(color=0.5)) == pytest.approx(0.5)
    assert ambiguity(vector(color=0.5)) > ambiguity(vector(color=0.9))


def test_los_atributos_no_medibles_no_entran_en_la_media():
    assert ambiguity(vector(color=0.5, peso=None)) == pytest.approx(0.5)


def test_un_vector_sin_nada_medible_no_tiene_ambiguedad_definida():
    assert ambiguity(vector(color=None)) is None
    assert ambiguity({}) is None


def test_la_ambiguedad_de_una_division_pondera_por_tamano():
    assert weighted_ambiguity([(90, 0.0), (10, 0.5)]) == pytest.approx(0.05)


def test_una_division_con_un_grupo_no_medible_no_es_evaluable():
    """Conservador a propósito: si un grupo no se puede medir, la ganancia
    aparente del resto es una ilusión, y una ilusión que siempre favorece
    dividir."""
    assert weighted_ambiguity([(90, 0.1), (10, None)]) is None


def test_la_ganancia_es_lo_que_baja_la_ambiguedad():
    assert gain(0.5, 0.05) == pytest.approx(0.45)
    assert gain(0.5, None) is None
    assert gain(None, 0.1) is None
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/profile/test_ambiguity.py -x` → `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`src/skudo/profile/partition.py` (primera mitad; la Task 5 añade el resto):

```python
"""Descubrimiento de subtipos: qué división del attribute set dice algo.

Un subtipo no es una etiqueta que alguien escriba, es una partición que cambia
materialmente QUÉ ATRIBUTOS SE LLENAN. La medida de "dice algo" es la
ambigüedad: un atributo presente en el 50 % de un grupo no permite ninguna
regla —marcaría mal a la mitad—, y uno presente en el 97 % sí. Dividir bien es
convertir un 50/50 en dos grupos de 97/3.
"""

from skudo.profile.coverage import Counts

# --- Umbrales -------------------------------------------------------------
#
# Son constantes del código y NO configuración por tenant. Un umbral que se
# puede aflojar por configuración termina aflojado: es la misma lección que la
# válvula del barrido de S0, y aquí aflojarlo produce reglas que marcan como
# defecto la variedad legítima de un catálogo. Cambiarlos es editar esta línea
# y que se vea en el diff. Los valores están calibrados contra el catálogo real
# del tenant piloto en `docs/superpowers/s1a-calibracion.md`.

# Productos mínimos en CADA grupo resultante. Por debajo, la cobertura del
# grupo es ruido: con 10 productos, un 90 % y un 100 % no se distinguen.
MIN_PARTICION = 50

# Cardinalidad máxima de un atributo para poder ser divisor. Un atributo con
# cincuenta valores no divide: pulveriza, y cada trozo cae bajo MIN_PARTICION.
MAX_CARD = 12

# Reducción mínima de ambigüedad para aceptar una división. Por debajo, lo que
# se ha encontrado es una fluctuación, y el precio de equivocarse es una regla
# de obligatoriedad aplicada a un grupo que no la cumple.
MIN_GANANCIA = 0.05


def ambiguity(vector: dict[str, Counts]) -> float | None:
    """Media de `min(c, 1-c)` sobre los atributos medibles del vector.

    Devuelve `None` si no hay ni un atributo medible: un grupo sobre el que no
    se puede afirmar nada no tiene ambigüedad baja, tiene ambigüedad
    desconocida, y son cosas distintas.
    """
    valores = [
        min(c.cobertura, 1.0 - c.cobertura)
        for c in vector.values()
        if c.cobertura is not None
    ]
    if not valores:
        return None
    return sum(valores) / len(valores)


def weighted_ambiguity(grupos: list[tuple[int, float | None]]) -> float | None:
    """Ambigüedad de una división, ponderada por el tamaño de cada grupo.

    Si CUALQUIER grupo no es medible, la división entera no lo es. La
    alternativa —ignorar ese grupo— hace que la ganancia aparente crezca cuanto
    más ignorancia haya, que es exactamente el incentivo al revés.
    """
    if not grupos:
        return None
    total = sum(n for n, _ in grupos)
    if total == 0:
        return None
    acumulado = 0.0
    for n, a in grupos:
        if a is None:
            return None
        acumulado += n * a
    return acumulado / total


def gain(antes: float | None, despues: float | None) -> float | None:
    """Cuánta ambigüedad quita la división. `None` si alguno de los dos falta."""
    if antes is None or despues is None:
        return None
    return antes - despues
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/profile/test_ambiguity.py -q` → PASS (8 tests).

- [ ] **Step 5: Sabotaje**

Hacer que `weighted_ambiguity` salte los grupos con `None` en vez de devolver `None`:
`test_una_division_con_un_grupo_no_medible_no_es_evaluable` debe fallar. Revertir.

- [ ] **Step 6: Commit**

```bash
git add src/skudo/profile/partition.py tests/profile/test_ambiguity.py
git commit -m "feat(s1a): ambiguedad y ganancia, con los umbrales como constantes"
```

---

### Task 5: Candidatos a divisor y la elección

**Files:**
- Modify: `src/skudo/profile/partition.py`
- Test: `tests/profile/test_partition.py`

**Interfaces:**
- Consumes: lo de la Task 4, `ProductoPerfilado` y `coverage_vector` de la Task 3.
- Produces: `Splitter(kind, key)`, `SIN_VALOR`, `SIN_CATEGORIA`,
  `split_value(product, splitter, depth_by_category) -> str`,
  `candidate_splitters(products, codes, frontend_input_by_code, depth_by_category) ->
  list[Splitter]`, `split(...) -> dict[str, list[ProductoPerfilado]]`, y
  `Choice(splitter, gain, reason, groups)` con
  `choose_splitter(products, codes, sets_by_code, frontend_input_by_code,
  depth_by_category) -> Choice`.

- [ ] **Step 1: Escribir el test que falla**

`tests/profile/test_partition.py`:

```python
import pytest

from skudo.profile.coverage import ProductoPerfilado
from skudo.profile.partition import (
    SIN_CATEGORIA,
    SIN_VALOR,
    Splitter,
    candidate_splitters,
    choose_splitter,
    split,
    split_value,
)

SETS = {c: frozenset({4}) for c in ("tipo", "talle", "voltaje", "color", "etiquetas")}
CODES = ("color", "etiquetas", "talle", "tipo", "voltaje")
INPUTS = {
    "tipo": "select",
    "talle": "select",
    "voltaje": "select",
    "color": "select",
    "etiquetas": "multiselect",
}
PROFUNDIDAD = {10: 1, 20: 2, 21: 2}


def p(sku, **attrs):
    cats = attrs.pop("_cats", ())
    return ProductoPerfilado(
        sku=sku, attributes=attrs, attribute_set_id=4, categorias=cats
    )


def catalogo(n_ropa=60, n_electro=60):
    """Un set que mezcla dos fichas: la ropa llena `talle`, el electro `voltaje`.

    El color va alternado a propósito, para que exista un candidato a divisor
    que NO informa de nada: si el perfilador eligiera por orden en vez de por
    ganancia, se quedaría con él. Y `etiquetas` lleva valores de verdad —dos,
    dentro del rango de cardinalidad admisible— para que lo ÚNICO que lo deje
    fuera de los candidatos sea su tipo de entrada. Sin valores, el test del
    multiselect pasaría por la razón equivocada: la de un atributo que nadie usa.
    """
    ropa = [
        p(
            f"r{i}",
            tipo="ropa",
            talle="M",
            color="negro" if i % 2 else "blanco",
            etiquetas="oferta" if i % 3 else "nuevo",
        )
        for i in range(n_ropa)
    ]
    electro = [
        p(
            f"e{i}",
            tipo="electro",
            voltaje="220",
            color="negro" if i % 2 else "blanco",
            etiquetas="oferta" if i % 3 else "nuevo",
        )
        for i in range(n_electro)
    ]
    return ropa + electro


def test_un_multiselect_no_es_candidato_a_divisor():
    """Un producto puede tener tres etiquetas a la vez: no parte el conjunto,
    lo solapa. Dividir por él pondría el mismo producto en dos grupos."""
    candidatos = candidate_splitters(catalogo(), CODES, INPUTS, PROFUNDIDAD)
    assert Splitter("atributo", "etiquetas") not in candidatos


def test_un_atributo_de_valor_unico_no_es_candidato():
    productos = [p(f"x{i}", tipo="ropa") for i in range(60)]
    assert Splitter("atributo", "tipo") not in candidate_splitters(
        productos, CODES, INPUTS, PROFUNDIDAD
    )


def test_un_atributo_demasiado_variado_no_es_candidato():
    productos = [p(f"x{i}", color=f"c{i}") for i in range(60)]
    assert Splitter("atributo", "color") not in candidate_splitters(
        productos, CODES, INPUTS, PROFUNDIDAD
    )


def test_los_candidatos_van_ordenados():
    candidatos = candidate_splitters(catalogo(), CODES, INPUTS, PROFUNDIDAD)
    assert candidatos == sorted(candidatos, key=lambda s: (s.kind, s.key))


def test_los_productos_sin_valor_forman_su_propio_grupo():
    """Descartarlos sesgaría la medición hacia los productos bien cargados, que
    son precisamente los que no hace falta analizar."""
    productos = [p(f"a{i}", tipo="ropa") for i in range(3)] + [p("b1")]
    grupos = split(productos, Splitter("atributo", "tipo"), PROFUNDIDAD)
    assert sorted(grupos) == [SIN_VALOR, "ropa"]
    assert [x.sku for x in grupos[SIN_VALOR]] == ["b1"]


def test_la_categoria_divisora_es_la_mas_profunda():
    producto = p("x", _cats=(10, 20))
    assert split_value(producto, Splitter("categoria", "categoria"), PROFUNDIDAD) == "20"


def test_el_empate_de_profundidad_se_rompe_por_id():
    producto = p("x", _cats=(21, 20))
    assert split_value(producto, Splitter("categoria", "categoria"), PROFUNDIDAD) == "20"


def test_un_producto_sin_categoria_tiene_su_grupo():
    assert (
        split_value(p("x"), Splitter("categoria", "categoria"), PROFUNDIDAD)
        == SIN_CATEGORIA
    )


def test_elige_el_divisor_que_separa_las_dos_fichas():
    eleccion = choose_splitter(catalogo(), CODES, SETS, INPUTS, PROFUNDIDAD)
    assert eleccion.splitter == Splitter("atributo", "tipo")
    assert eleccion.reason == "elegido"
    assert eleccion.gain == pytest.approx(0.2)
    assert sorted(eleccion.groups) == ["electro", "ropa"]


def test_un_set_homogeneo_declara_que_no_hay_subtipo():
    """Decir que NO hay subtipo vale tanto como encontrarlo: distingue un set
    homogéneo de uno que nadie supo dividir."""
    productos = [p(f"x{i}", tipo="ropa", talle="M") for i in range(120)]
    eleccion = choose_splitter(productos, CODES, SETS, INPUTS, PROFUNDIDAD)
    assert eleccion.splitter is None
    assert eleccion.reason in {"sin candidatos", "ganancia insuficiente"}


def test_una_division_que_deja_un_grupo_pequeno_se_rechaza():
    """El divisor informativo existe y separa bien, pero un lado tiene cinco
    productos: con eso, su cobertura es ruido y la regla que saldría sería
    ruido con aspecto de norma."""
    ropa = [p(f"r{i}", tipo="ropa", talle="M", color="negro") for i in range(120)]
    electro = [p(f"e{i}", tipo="electro", voltaje="220", color="negro") for i in range(5)]
    eleccion = choose_splitter(ropa + electro, CODES, SETS, INPUTS, PROFUNDIDAD)
    assert eleccion.splitter is None
    assert eleccion.reason == "grupo pequeno"


def test_la_eleccion_es_determinista_ante_el_empate():
    """Dos divisores con la misma ganancia: gana el primero por orden
    alfabético, siempre, aunque la lista de productos llegue barajada."""
    izquierda = [p(f"a{i}", talle="M", tipo="ropa", color="negro") for i in range(60)]
    derecha = [p(f"b{i}", voltaje="220", tipo="electro", color="blanco") for i in range(60)]
    directo = choose_splitter(izquierda + derecha, CODES, SETS, INPUTS, PROFUNDIDAD)
    invertido = choose_splitter(derecha + izquierda, CODES, SETS, INPUTS, PROFUNDIDAD)
    assert directo.splitter == invertido.splitter
    assert directo.splitter.key == "color"
    assert directo.gain == pytest.approx(invertido.gain)
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/profile/test_partition.py -x` → `ImportError`.

- [ ] **Step 3: Implementar**

Añadir a `src/skudo/profile/partition.py`:

```python
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from skudo.profile.coverage import ProductoPerfilado, coverage_vector

# Etiquetas de los grupos que NO tienen valor de divisor. Son grupos de primera
# clase: los productos a los que les falta el propio divisor suelen ser los peor
# cargados del catálogo, o sea exactamente los que hay que medir.
SIN_VALOR = "(sin valor)"
SIN_CATEGORIA = "(sin categoria)"

# Sólo un atributo de valor único puede partir un conjunto. Un multiselect
# solapa grupos en vez de partirlos, y un producto acabaría contado dos veces.
INPUTS_DIVISIBLES = frozenset({"select", "boolean"})


@dataclass(frozen=True, order=True)
class Splitter:
    kind: str
    key: str


@dataclass(frozen=True)
class Choice:
    splitter: Splitter | None
    gain: float | None
    reason: str
    groups: dict[str, list[ProductoPerfilado]]


def split_value(
    product: ProductoPerfilado,
    splitter: Splitter,
    depth_by_category: dict[int, int],
) -> str:
    """En qué grupo cae un producto. Siempre devuelve un grupo: nadie se pierde."""
    if splitter.kind == "categoria":
        conocidas = [c for c in product.categorias if c in depth_by_category]
        if not conocidas:
            return SIN_CATEGORIA
        # La más profunda; empate por id menor, para que el resultado no dependa
        # del orden en que el espejo devolvió las asignaciones.
        elegida = min(conocidas, key=lambda c: (-depth_by_category[c], c))
        return str(elegida)
    valor = product.attributes.get(splitter.key)
    if valor is None or (isinstance(valor, str) and valor.strip() == ""):
        return SIN_VALOR
    return str(valor)


def split(
    products: Sequence[ProductoPerfilado],
    splitter: Splitter,
    depth_by_category: dict[int, int],
) -> dict[str, list[ProductoPerfilado]]:
    grupos: dict[str, list[ProductoPerfilado]] = defaultdict(list)
    for product in products:
        grupos[split_value(product, splitter, depth_by_category)].append(product)
    return dict(sorted(grupos.items()))


def candidate_splitters(
    products: Sequence[ProductoPerfilado],
    codes: Sequence[str],
    frontend_input_by_code: dict[str, str],
    depth_by_category: dict[int, int],
) -> list[Splitter]:
    """Los divisores que vale la pena probar, en orden estable."""
    candidatos: list[Splitter] = []
    for code in sorted(codes):
        if frontend_input_by_code.get(code) not in INPUTS_DIVISIBLES:
            continue
        valores = {
            v
            for v in (
                split_value(p, Splitter("atributo", code), depth_by_category)
                for p in products
            )
            if v != SIN_VALOR
        }
        if 2 <= len(valores) <= MAX_CARD:
            candidatos.append(Splitter("atributo", code))
    por_categoria = {
        v
        for v in (
            split_value(p, Splitter("categoria", "categoria"), depth_by_category)
            for p in products
        )
        if v != SIN_CATEGORIA
    }
    if 2 <= len(por_categoria) <= MAX_CARD:
        candidatos.append(Splitter("categoria", "categoria"))
    return sorted(candidatos)


def choose_splitter(
    products: Sequence[ProductoPerfilado],
    codes: Sequence[str],
    sets_by_code: dict[str, frozenset[int]],
    frontend_input_by_code: dict[str, str],
    depth_by_category: dict[int, int],
) -> Choice:
    """El divisor que más ambigüedad quita, si quita bastante y no pulveriza.

    La razón viaja con la decisión. Un perfil que sólo dice "sin subtipo" no
    permite discutir nada; uno que dice "ganancia insuficiente: 0,02" permite
    mirar el umbral y decidir si el problema es el catálogo o la constante.
    """
    base = ambiguity(coverage_vector(products, codes, sets_by_code))
    candidatos = candidate_splitters(
        products, codes, frontend_input_by_code, depth_by_category
    )
    if not candidatos:
        return Choice(None, None, "sin candidatos", {})

    mejor: Choice | None = None
    hubo_grupo_pequeno = False
    for splitter in candidatos:
        grupos = split(products, splitter, depth_by_category)
        if min(len(g) for g in grupos.values()) < MIN_PARTICION:
            hubo_grupo_pequeno = True
            continue
        ponderada = weighted_ambiguity(
            [
                (len(g), ambiguity(coverage_vector(g, codes, sets_by_code)))
                for _, g in sorted(grupos.items())
            ]
        )
        ganancia = gain(base, ponderada)
        if ganancia is None:
            continue
        # Empate: gana el primero del orden estable de `candidatos`, no el
        # último visto. Sin este `>` estricto, el resultado dependería del orden
        # de iteración y dos pasadas iguales podrían diferir.
        if mejor is None or ganancia > mejor.gain:
            mejor = Choice(splitter, ganancia, "elegido", grupos)

    if mejor is None:
        return Choice(None, None, "grupo pequeno" if hubo_grupo_pequeno else "sin candidatos", {})
    if mejor.gain < MIN_GANANCIA:
        return Choice(None, mejor.gain, "ganancia insuficiente", {})
    return mejor
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/profile/test_partition.py -q` → PASS (12 tests).

- [ ] **Step 5: Sabotaje**

Tres sabotajes, uno por invariante; cada uno debe romper tests distintos:
1. Aceptar `multiselect` en `INPUTS_DIVISIBLES`.
2. Descartar los productos sin valor en vez de darles grupo.
3. Cambiar `ganancia > mejor.gain` por `>=`, y comprobar que
   `test_la_eleccion_es_determinista_ante_el_empate` falla.

Revertir los tres.

- [ ] **Step 6: Commit**

```bash
git add src/skudo/profile/partition.py tests/profile/test_partition.py
git commit -m "feat(s1a): el divisor se elige por ganancia, y el 'no hay subtipo' se justifica"
```

---

### Task 6: Distribuciones y poder discriminante

**Files:**
- Create: `src/skudo/profile/distribution.py`
- Test: `tests/profile/test_distribution.py`

**Interfaces:**
- Produces: `Lectura(valor, motivo)`, `parse_number(raw) -> Lectura`,
  `percentil(valores_ordenados, k) -> float`, `Stats` (dataclass con los campos de la tabla
  `profile_value_stats`), y `value_stats(valores, frontend_input) -> Stats`.

- [ ] **Step 1: Escribir el test que falla**

`tests/profile/test_distribution.py`:

```python
import pytest

from skudo.profile.distribution import Lectura, parse_number, percentil, value_stats


def test_lee_un_entero_y_un_decimal_con_punto():
    assert parse_number("12") == Lectura(12.0, "leido")
    assert parse_number("2.5") == Lectura(2.5, "leido")


def test_lee_la_coma_decimal():
    """PY y BR escriben 2,5. Rechazarlo tiraría media distribución."""
    assert parse_number("2,5") == Lectura(2.5, "leido")


def test_con_los_dos_separadores_el_ultimo_manda():
    """Funciona para las dos convenciones sin adivinar cuál se usó."""
    assert parse_number("1.234,56") == Lectura(1234.56, "leido")
    assert parse_number("1,234.56") == Lectura(1234.56, "leido")


def test_un_separador_con_tres_decimales_es_ambiguo_y_no_se_adivina():
    """`1,250` es 1250 o 1,25 según quién lo escribió, y la diferencia es de
    mil veces: exactamente la sospecha de conversión que el spec quiere
    detectar, no propagar. Se cuenta aparte y no entra en la distribución."""
    assert parse_number("1,250") == Lectura(None, "ambiguo")
    assert parse_number("1.250") == Lectura(None, "ambiguo")


def test_lo_que_no_es_un_numero_se_dice_asi():
    assert parse_number("2.5 kg") == Lectura(None, "no_numerico")
    assert parse_number("") == Lectura(None, "no_numerico")
    assert parse_number("N/A") == Lectura(None, "no_numerico")


def test_el_cero_se_lee_como_cero():
    assert parse_number("0") == Lectura(0.0, "leido")


def test_percentil_por_rango_mas_cercano():
    valores = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentil(valores, 50) == 3.0
    assert percentil(valores, 5) == 1.0
    assert percentil(valores, 95) == 5.0
    assert percentil([7.0], 50) == 7.0


def test_un_atributo_numerico_trae_su_distribucion():
    stats = value_stats([str(v) for v in range(1, 101)], "text")
    assert stats.kind == "numerico"
    assert stats.n_present == 100
    assert stats.minimum == 1.0
    assert stats.maximum == 100.0
    assert stats.p50 == pytest.approx(50.0)


def test_los_ambiguos_se_cuentan_y_no_contaminan():
    stats = value_stats(["1", "2", "3", "1,250"], "text")
    assert stats.n_ambiguous == 1
    assert stats.maximum == 3.0


def test_un_atributo_de_opcion_trae_cardinalidad_y_poder():
    stats = value_stats(["rojo"] * 9 + ["azul"], "select")
    assert stats.kind == "opcion"
    assert stats.distinct_values == 2
    assert stats.mode_share == pytest.approx(0.9)
    assert stats.discriminating_power == pytest.approx(0.1)


def test_un_atributo_que_vale_lo_mismo_para_todos_no_discrimina():
    """Es la base de la plantilla de nombre: meter en el nombre un atributo que
    todos comparten no distingue nada."""
    stats = value_stats(["negro"] * 50, "select")
    assert stats.discriminating_power == 0.0


def test_los_valores_mas_frecuentes_van_ordenados_y_acotados():
    stats = value_stats(["a"] * 5 + ["b"] * 3 + ["c"] * 2 + [f"x{i}" for i in range(30)], "select")
    assert [v for v, _ in stats.top_values] == ["a", "b", "c"] + sorted(
        f"x{i}" for i in range(30)
    )[: 10 - 3]
    assert len(stats.top_values) == 10


def test_sin_valores_presentes_todo_es_nulo_y_nada_es_cero():
    stats = value_stats([], "select")
    assert stats.n_present == 0
    assert stats.distinct_values is None
    assert stats.discriminating_power is None
    assert stats.minimum is None
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/profile/test_distribution.py -x` → `ModuleNotFoundError`.

- [ ] **Step 3: Implementar**

`src/skudo/profile/distribution.py`:

```python
"""Qué valores toma un atributo, sin adivinar ninguno.

El parseo numérico es el punto donde este módulo puede hacer daño: leer
`1,250` como 1,25 cuando eran 1250 gramos produce un dato falso con aspecto de
medición, y la distribución entera hereda el error. Cuando las dos lecturas son
plausibles, la respuesta es "ambiguo" y el valor se cuenta aparte.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

# Cuántos valores distintos se guardan de cada atributo. Suficiente para que un
# humano reconozca el patrón en la curación, y acotado para que el perfil no se
# convierta en una copia del catálogo.
TOP_VALORES = 10

# Proporción de valores presentes que tienen que leerse como número para tratar
# el atributo como numérico. Magento declara casi todo como `text`, así que el
# tipo hay que medirlo; y un puñado de números sueltos dentro de un campo de
# texto no lo convierte en una magnitud.
MIN_SHARE_NUMERICO = 0.8

_SOLO_NUMERO = re.compile(r"^[+-]?\d+([.,]\d+)*$")


@dataclass(frozen=True)
class Lectura:
    valor: float | None
    motivo: str  # "leido" | "ambiguo" | "no_numerico"


def parse_number(raw: object) -> Lectura:
    texto = str(raw).strip()
    if not _SOLO_NUMERO.match(texto):
        return Lectura(None, "no_numerico")
    separadores = [c for c in texto if c in ".,"]
    if not separadores:
        return Lectura(float(texto), "leido")
    if len(set(separadores)) == 2:
        # Hay punto y coma: el ÚLTIMO es el decimal, en las dos convenciones.
        ultimo = max(texto.rfind("."), texto.rfind(","))
        entero = re.sub(r"[.,]", "", texto[:ultimo])
        return Lectura(float(f"{entero}.{texto[ultimo + 1:]}"), "leido")
    if len(separadores) > 1:
        # Un mismo separador repetido sólo puede ser de miles: 1.234.567.
        return Lectura(float(re.sub(r"[.,]", "", texto)), "leido")
    posicion = max(texto.rfind("."), texto.rfind(","))
    decimales = len(texto) - posicion - 1
    if decimales == 3:
        return Lectura(None, "ambiguo")
    return Lectura(float(texto[:posicion] + "." + texto[posicion + 1 :]), "leido")


def percentil(valores_ordenados: Sequence[float], k: int) -> float:
    """Rango más cercano, sin interpolar.

    Interpolar inventa un valor que nadie tiene, y estas distribuciones se usan
    para decidir si un peso es implausible: el umbral tiene que ser un valor
    que exista en el catálogo.
    """
    n = len(valores_ordenados)
    indice = max(1, min(n, -(-k * n // 100)))
    return valores_ordenados[indice - 1]


@dataclass(frozen=True)
class Stats:
    kind: str
    n_present: int
    n_ambiguous: int = 0
    minimum: float | None = None
    p05: float | None = None
    p50: float | None = None
    p95: float | None = None
    maximum: float | None = None
    distinct_values: int | None = None
    mode_share: float | None = None
    discriminating_power: float | None = None
    top_values: list = field(default_factory=list)


def value_stats(valores: Sequence[object], frontend_input: str) -> Stats:
    """La distribución de UN atributo dentro de UNA partición."""
    presentes = [v for v in valores]
    if not presentes:
        return Stats(kind="texto", n_present=0)

    lecturas = [parse_number(v) for v in presentes]
    numeros = sorted(l.valor for l in lecturas if l.motivo == "leido")
    ambiguos = sum(1 for l in lecturas if l.motivo == "ambiguo")

    conteo = Counter(str(v) for v in presentes)
    # Orden estable: primero por frecuencia descendente, luego alfabético. Sin
    # el desempate alfabético, dos pasadas iguales podrían listar distinto.
    top = sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_VALORES]
    moda = max(conteo.values()) / len(presentes)

    es_numerico = bool(numeros) and len(numeros) >= MIN_SHARE_NUMERICO * len(presentes)
    if es_numerico:
        kind = "numerico"
    elif frontend_input in {"select", "multiselect", "boolean"}:
        kind = "opcion"
    else:
        kind = "texto"

    return Stats(
        kind=kind,
        n_present=len(presentes),
        n_ambiguous=ambiguos,
        minimum=numeros[0] if numeros else None,
        p05=percentil(numeros, 5) if numeros else None,
        p50=percentil(numeros, 50) if numeros else None,
        p95=percentil(numeros, 95) if numeros else None,
        maximum=numeros[-1] if numeros else None,
        distinct_values=len(conteo),
        mode_share=moda,
        discriminating_power=1.0 - moda,
        top_values=[[valor, n] for valor, n in top],
    )
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/profile/test_distribution.py -q` → PASS (13 tests).

- [ ] **Step 5: Sabotaje**

Hacer que `parse_number` lea los tres decimales como decimales en vez de devolver
`ambiguo`: `test_un_separador_con_tres_decimales_es_ambiguo_y_no_se_adivina` y
`test_los_ambiguos_se_cuentan_y_no_contaminan` deben fallar. Revertir.

- [ ] **Step 6: Commit**

```bash
git add src/skudo/profile/distribution.py tests/profile/test_distribution.py
git commit -m "feat(s1a): distribuciones, con el 1,250 declarado ambiguo en vez de adivinado"
```

---

### Task 7: La pasada

**Files:**
- Create: `src/skudo/profile/run.py`
- Test: `tests/profile/test_run.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: `profile_store_view(session, tenant_id, store_view_magento_id) -> ProfileRun`,
  que deja escritas las filas de las cuatro tablas y sella la pasada con su digest.

- [ ] **Step 1: Los ayudantes de siembra, en el módulo que ya existe para eso**

Añadir a `tests/skudo_testing.py` —donde vive el andamiaje de tests y donde ya está
`upsert_record`— los tres ayudantes que usan las Tasks 7 y 8. Se ponen ahí, y no en un
`tests/profile/helpers.py`, porque `tests/` no es un paquete: `skudo_testing` es el único
módulo de tests que se importa por nombre, y duplicar el mecanismo crearía dos convenciones.

```python
def preparar(
    session,
    atributos=(("tipo", "select", [4]), ("talle", "select", [4]), ("voltaje", "select", [4])),
):
    """Un tenant con atributos en el espejo. Sin ellos todo sale `desconocido`."""
    from skudo.mirror.attributes import upsert_attribute
    from skudo.mirror.models import Tenant

    tenant = Tenant(code="acme", name="A", base_url="http://a.test", token_env_var="X")
    session.add(tenant)
    session.flush()
    for code, entrada, sets in atributos:
        upsert_attribute(
            session,
            tenant.id,
            {
                "code": code,
                "label": code,
                "frontend_input": entrada,
                "declared_scope": "global",
                "is_filterable": False,
                "is_required": False,
                "attribute_set_ids": sets,
            },
            sync_generation=1,
        )
    return tenant


def escribir(session, tenant, store_view, sku, attrs, set_id=4):
    from skudo.mirror.products import ProductIdentity

    upsert_record(
        session,
        tenant.id,
        store_view,
        ProductIdentity(sku=sku),
        attrs,
        {code: "global" for code in attrs},
        None,
        attribute_set_id=set_id,
    )


def sembrar(session, tenant, n_ropa=60, n_electro=60, store_view=1):
    """Dos fichas distintas dentro del mismo attribute set: el caso que el
    descubrimiento de subtipos existe para encontrar."""
    for i in range(n_ropa):
        escribir(session, tenant, store_view, f"r{i}", {"tipo": "ropa", "talle": "M"})
    for i in range(n_electro):
        escribir(
            session, tenant, store_view, f"e{i}", {"tipo": "electro", "voltaje": "220"}
        )
```

- [ ] **Step 2: Escribir el test que falla**

`tests/profile/test_run.py`:

```python
from sqlalchemy import select

from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun
from skudo.profile.run import profile_store_view
from skudo_testing import escribir, preparar, sembrar


def test_la_pasada_encuentra_el_subtipo_y_lo_deja_escrito(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)

    run = profile_store_view(db_session, tenant.id, 1)

    particiones = db_session.scalars(
        select(ProfilePartition).where(ProfilePartition.run_id == run.id)
    ).all()
    assert {p.splitter_value for p in particiones} == {"ropa", "electro"}
    assert {p.splitter_key for p in particiones} == {"tipo"}
    assert all(p.decision_reason == "elegido" for p in particiones)


def test_la_cobertura_del_subtipo_separa_las_dos_fichas(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = profile_store_view(db_session, tenant.id, 1)

    filas = db_session.execute(
        select(ProfilePartition.splitter_value, AttributeCoverage.attribute_code,
               AttributeCoverage.coverage)
        .join(AttributeCoverage, AttributeCoverage.partition_id == ProfilePartition.id)
        .where(ProfilePartition.run_id == run.id)
    ).all()
    cobertura = {(v, c): cov for v, c, cov in filas}
    assert cobertura[("ropa", "talle")] == 1.0
    assert cobertura[("ropa", "voltaje")] == 0.0
    assert cobertura[("electro", "voltaje")] == 1.0


def test_dos_pasadas_sobre_el_mismo_espejo_dan_el_mismo_sello(db_session):
    """La propiedad que hace discutible una regla: si el perfil no es
    reproducible, cualquier desacuerdo sobre una regla acaba en 'a mí me salió
    distinto' y no hay forma de cerrarlo."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    primera = profile_store_view(db_session, tenant.id, 1)
    segunda = profile_store_view(db_session, tenant.id, 1)
    assert primera.digest == segunda.digest
    assert primera.id != segunda.id


def test_un_cambio_en_el_espejo_cambia_el_sello(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    antes = profile_store_view(db_session, tenant.id, 1)
    escribir(db_session, tenant, 1, "r0", {"tipo": "ropa"})
    despues = profile_store_view(db_session, tenant.id, 1)
    assert antes.digest != despues.digest


def test_las_dos_store_views_se_perfilan_por_separado(db_session):
    """El hallazgo que el producto existe para encontrar: el mismo atributo al
    100 % en PY y al 0 % en BR. Promediarlo lo borraría."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    for i in range(120):
        escribir(
            db_session,
            tenant,
            3,
            f"r{i}" if i < 60 else f"e{i - 60}",
            {"tipo": "ropa" if i < 60 else "electro"},
        )
    py = profile_store_view(db_session, tenant.id, 1)
    br = profile_store_view(db_session, tenant.id, 3)
    assert py.digest != br.digest
    assert py.store_view_magento_id == 1 and br.store_view_magento_id == 3


def test_los_productos_sin_attribute_set_van_a_su_propia_particion(db_session):
    """No se descartan y no se mezclan: se cuentan, porque un catálogo con
    muchos de ellos tiene un problema de ingesta que el perfil debe gritar."""
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    escribir(db_session, tenant, 1, "huerfano", {"tipo": "ropa"}, set_id=None)
    run = profile_store_view(db_session, tenant.id, 1)
    desconocida = db_session.scalars(
        select(ProfilePartition).where(
            ProfilePartition.run_id == run.id,
            ProfilePartition.splitter_kind == "set_desconocido",
        )
    ).one()
    assert desconocida.product_count == 1
    assert desconocida.attribute_set_id is None


def test_la_pasada_guarda_los_umbrales_con_los_que_midio(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    run = profile_store_view(db_session, tenant.id, 1)
    assert run.thresholds["MIN_PARTICION"] == 50
    assert run.thresholds["MAX_CARD"] == 12
    assert run.thresholds["MIN_GANANCIA"] == 0.05
    assert run.finished_at is not None
```

- [ ] **Step 3: Verificar que falla**

Run: `uv run pytest tests/profile/test_run.py -x` → `ModuleNotFoundError`.

- [ ] **Step 4: Implementar**

`src/skudo/profile/run.py`:

```python
"""La pasada del perfilador sobre una store view.

Recorre el espejo UN ATTRIBUTE SET A LA VEZ. No es un detalle de estilo: el
catálogo piloto tiene 228.881 productos por store view y cargarlos todos en
memoria para agruparlos después convierte una pasada de minutos en una que no
termina en una máquina normal. Cargar un set —el mayor son decenas de miles de
filas— acota la memoria por el set más grande y no por el catálogo.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute, Category, ProductCategoryAssignment, ProductRecord
from skudo.profile.coverage import ProductoPerfilado, coverage_vector
from skudo.profile.distribution import value_stats
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun, ValueStats
from skudo.profile.partition import (
    MAX_CARD,
    MIN_GANANCIA,
    MIN_PARTICION,
    SIN_VALOR,
    choose_splitter,
)
from skudo.profile.states import codes_by_set, sets_by_code


def _depth_by_category(session: Session, tenant_id: int) -> dict[int, int]:
    """Profundidad de cada categoría, para elegir la más específica de un producto."""
    filas = session.execute(
        select(Category.magento_id, Category.path).where(Category.tenant_id == tenant_id)
    ).all()
    return {magento_id: len(path or []) for magento_id, path in filas}


def _frontend_inputs(session: Session, tenant_id: int) -> dict[str, str]:
    filas = session.execute(
        select(Attribute.code, Attribute.frontend_input).where(
            Attribute.tenant_id == tenant_id
        )
    ).all()
    return dict(filas)


def _categorias_por_sku(session: Session, tenant_id: int) -> dict[str, tuple[int, ...]]:
    filas = session.execute(
        select(
            ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id
        ).where(ProductCategoryAssignment.tenant_id == tenant_id)
    ).all()
    acumulado: dict[str, list[int]] = {}
    for sku, categoria in filas:
        acumulado.setdefault(sku, []).append(categoria)
    return {sku: tuple(sorted(cats)) for sku, cats in acumulado.items()}


def _productos_del_set(
    session: Session,
    tenant_id: int,
    store_view_magento_id: int,
    attribute_set_id: int | None,
    categorias: dict[str, tuple[int, ...]],
) -> list[ProductoPerfilado]:
    condicion = (
        ProductRecord.attribute_set_id.is_(None)
        if attribute_set_id is None
        else ProductRecord.attribute_set_id == attribute_set_id
    )
    filas = session.execute(
        select(ProductRecord.sku, ProductRecord.attributes, ProductRecord.attribute_set_id)
        .where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
            condicion,
        )
        .order_by(ProductRecord.sku)  # el determinismo empieza aquí
    ).all()
    return [
        ProductoPerfilado(
            sku=sku,
            attributes=attributes or {},
            attribute_set_id=set_id,
            categorias=categorias.get(sku, ()),
        )
        for sku, attributes, set_id in filas
    ]


def profile_store_view(
    session: Session, tenant_id: int, store_view_magento_id: int
) -> ProfileRun:
    """Perfila una store view entera y devuelve la pasada ya sellada."""
    generacion = session.scalar(
        select(func.coalesce(func.max(ProductRecord.sync_generation), 0)).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
    total = session.scalar(
        select(func.count()).select_from(ProductRecord).where(
            ProductRecord.tenant_id == tenant_id,
            ProductRecord.store_view_magento_id == store_view_magento_id,
        )
    )
    run = ProfileRun(
        tenant_id=tenant_id,
        store_view_magento_id=store_view_magento_id,
        mirror_sync_generation=generacion or 0,
        thresholds={
            "MIN_PARTICION": MIN_PARTICION,
            "MAX_CARD": MAX_CARD,
            "MIN_GANANCIA": MIN_GANANCIA,
        },
        product_count=total or 0,
    )
    session.add(run)
    session.flush()

    por_codigo = sets_by_code(session, tenant_id)
    por_set = codes_by_set(session, tenant_id)
    entradas = _frontend_inputs(session, tenant_id)
    profundidad = _depth_by_category(session, tenant_id)
    categorias = _categorias_por_sku(session, tenant_id)

    sets = [
        s
        for (s,) in session.execute(
            select(ProductRecord.attribute_set_id)
            .where(
                ProductRecord.tenant_id == tenant_id,
                ProductRecord.store_view_magento_id == store_view_magento_id,
            )
            .distinct()
            .order_by(ProductRecord.attribute_set_id)
        ).all()
    ]

    huella: list = []
    for attribute_set_id in sets:
        productos = _productos_del_set(
            session, tenant_id, store_view_magento_id, attribute_set_id, categorias
        )
        if attribute_set_id is None:
            # Sin set no se puede distinguir vacío de no_aplica: se cuenta y no
            # se mide. Inventar una cobertura aquí sería inventar un hecho.
            _escribir_particion(
                session, run, None, "set_desconocido", None, None,
                len(productos), None, "set desconocido", {}, {}, huella,
            )
            continue

        codes = por_set.get(attribute_set_id, ())
        eleccion = choose_splitter(productos, codes, por_codigo, entradas, profundidad)
        grupos = eleccion.groups or {SIN_VALOR: productos}
        for valor, grupo in sorted(grupos.items()):
            vector = coverage_vector(grupo, codes, por_codigo)
            stats = {
                code: value_stats(
                    [
                        g.attributes[code]
                        for g in grupo
                        if str(g.attributes.get(code, "")).strip() != ""
                    ],
                    entradas.get(code, "text"),
                )
                for code in codes
            }
            _escribir_particion(
                session,
                run,
                attribute_set_id,
                "ninguno" if eleccion.splitter is None else eleccion.splitter.kind,
                None if eleccion.splitter is None else eleccion.splitter.key,
                None if eleccion.splitter is None else valor,
                len(grupo),
                None,
                eleccion.reason,
                vector,
                stats,
                huella,
            )

    run.digest = hashlib.sha256(
        json.dumps(huella, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    run.finished_at = datetime.now(timezone.utc)
    session.flush()
    return run


def _escribir_particion(
    session, run, attribute_set_id, kind, key, valor, n, ambiguedad, razon, vector, stats, huella
) -> None:
    """Escribe una partición con su cobertura y sus distribuciones.

    Va alimentando `huella` con lo mismo que escribe: el sello de la pasada se
    calcula sobre lo que de verdad quedó en la base, no sobre una segunda
    versión de los datos que podría divergir sin que nadie se entere.
    """
    from skudo.profile.partition import ambiguity

    calculada = ambiguity(vector) if vector else ambiguedad
    particion = ProfilePartition(
        run_id=run.id,
        attribute_set_id=attribute_set_id,
        splitter_kind=kind,
        splitter_key=key,
        splitter_value=valor,
        product_count=n,
        ambiguity=calculada,
        decision_reason=razon,
    )
    session.add(particion)
    session.flush()
    huella.append([attribute_set_id, kind, key, valor, n, calculada, razon])

    for code in sorted(vector):
        cuenta = vector[code]
        session.add(
            AttributeCoverage(
                partition_id=particion.id,
                attribute_code=code,
                presente=cuenta.presente,
                vacio=cuenta.vacio,
                no_aplica=cuenta.no_aplica,
                desconocido=cuenta.desconocido,
                coverage=cuenta.cobertura,
            )
        )
        huella.append([code, cuenta.presente, cuenta.vacio, cuenta.desconocido, cuenta.cobertura])

    for code in sorted(stats):
        s = stats[code]
        session.add(
            ValueStats(
                partition_id=particion.id,
                attribute_code=code,
                kind=s.kind,
                n_present=s.n_present,
                n_ambiguous=s.n_ambiguous,
                minimum=s.minimum,
                p05=s.p05,
                p50=s.p50,
                p95=s.p95,
                maximum=s.maximum,
                distinct_values=s.distinct_values,
                mode_share=s.mode_share,
                discriminating_power=s.discriminating_power,
                top_values=s.top_values,
            )
        )
        huella.append([code, s.kind, s.n_present, s.n_ambiguous, s.p50, s.discriminating_power])
    session.flush()
```

- [ ] **Step 5: Verificar**

Run: `uv run pytest tests/profile/test_run.py -q` → PASS (7 tests).

- [ ] **Step 6: Sabotaje**

1. Quitar el `order_by(ProductRecord.sku)` y comprobar que el test de reproducibilidad
   sigue pasando **sólo** porque Postgres devuelve el mismo orden por casualidad; dejarlo
   puesto de todas formas y anotar en el informe de la tarea que ese test no protege el
   orden por sí solo.
2. Mezclar los productos sin set en la partición del set 4: el test de la partición
   `set_desconocido` debe fallar.

- [ ] **Step 7: Commit**

```bash
git add src/skudo/profile/run.py tests/profile/test_run.py
git commit -m "feat(s1a): la pasada, sellada y por store view"
```

---

### Task 8: El comando `profile` y su informe

**Files:**
- Create: `src/skudo/profile/report.py`
- Modify: `src/skudo/cli.py`
- Test: `tests/profile/test_report.py`, `tests/test_cli.py` (añadir)

**Interfaces:**
- Consumes: `profile_store_view` de la Task 7.
- Produces: `profile_report(session, run) -> dict`, y el subcomando
  `skudo profile --tenant <code> [--stores 1,3]`.

- [ ] **Step 1: Escribir los tests que fallan**

`tests/profile/test_report.py`:

```python
from skudo.profile.report import profile_report
from skudo.profile.run import profile_store_view
from skudo_testing import escribir, preparar, sembrar


def test_el_informe_dice_cuantos_sets_tienen_subtipo_y_por_que_no_los_demas(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    for i in range(70):
        escribir(db_session, tenant, 1, f"h{i}", {"tipo": "hogar"}, set_id=9)

    informe = profile_report(db_session, profile_store_view(db_session, tenant.id, 1))

    assert informe["productos"] == 190
    assert informe["sets"] == 2
    assert informe["con_subtipo"] == 1
    assert informe["razones"]["elegido"] == 1
    assert sum(informe["razones"].values()) == 2
    assert informe["divisores"] == {"tipo": 1}


def test_el_informe_cuenta_los_productos_sin_set(db_session):
    tenant = preparar(db_session)
    sembrar(db_session, tenant)
    escribir(db_session, tenant, 1, "huerfano", {"tipo": "ropa"}, set_id=None)
    informe = profile_report(db_session, profile_store_view(db_session, tenant.id, 1))
    assert informe["sin_attribute_set"] == 1
```

En `tests/test_cli.py`, siguiendo el estilo de los comandos existentes:

```python
def test_profile_no_necesita_token(cli_db, monkeypatch, capsys):
    """El perfilador no habla con Magento: exigir el token sería inventar una
    dependencia y dejar el comando inutilizable en una máquina de análisis que
    sólo tiene acceso al espejo."""
    _register()
    _run(["probe", "--tenant", "demo"], FakeMagento())
    _run(["full-sync", "--tenant", "demo", "--stores", "1"], FakeMagento())
    capsys.readouterr()
    monkeypatch.delenv(TOKEN_VAR, raising=False)

    assert _run(["profile", "--tenant", "demo", "--stores", "1"]) == EXIT_OK
    salida = json.loads(capsys.readouterr().out)
    assert salida["1"]["digest"]
    assert salida["1"]["umbrales"]["MIN_PARTICION"] == 50


def test_profile_de_un_tenant_desconocido(cli_db):
    assert _run(["profile", "--tenant", "noexiste"]) == EXIT_UNKNOWN_TENANT
```

Los ayudantes `_register`, `_run`, `FakeMagento`, `TOKEN_VAR` y el fixture `cli_db` ya
existen en `tests/test_cli.py`; el test nuevo usa los mismos y no introduce andamiaje.

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/profile/test_report.py tests/test_cli.py -x`
Expected: FAIL (`ModuleNotFoundError` y `invalid choice: 'profile'`).

- [ ] **Step 3: Implementar el informe**

`src/skudo/profile/report.py`:

```python
"""El resumen legible de una pasada.

Lo que hace útil este informe no es cuántos subtipos encontró, sino cuántos NO
y por qué: un set sin subtipo porque es homogéneo no necesita nada, y uno sin
subtipo porque todos sus candidatos dejaban grupos de treinta productos es una
petición de más catálogo o de otro umbral. Sin la razón, los dos se ven igual.
"""

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.profile.models import ProfilePartition, ProfileRun


def profile_report(session: Session, run: ProfileRun) -> dict:
    particiones = session.scalars(
        select(ProfilePartition).where(ProfilePartition.run_id == run.id)
    ).all()

    por_set: dict[int | None, list[ProfilePartition]] = {}
    for particion in particiones:
        por_set.setdefault(particion.attribute_set_id, []).append(particion)

    razones: Counter = Counter()
    divisores: Counter = Counter()
    con_subtipo = 0
    sin_set = 0
    for attribute_set_id, grupo in por_set.items():
        if attribute_set_id is None:
            sin_set = sum(p.product_count for p in grupo)
            continue
        razon = grupo[0].decision_reason
        razones[razon] += 1
        if razon == "elegido":
            con_subtipo += 1
            divisores[grupo[0].splitter_key] += 1

    return {
        "run_id": run.id,
        "store_view": run.store_view_magento_id,
        "generacion_espejo": run.mirror_sync_generation,
        "digest": run.digest,
        "productos": run.product_count,
        "sets": sum(1 for k in por_set if k is not None),
        "particiones": len(particiones),
        "con_subtipo": con_subtipo,
        "razones": dict(sorted(razones.items())),
        "divisores": dict(sorted(divisores.items())),
        "sin_attribute_set": sin_set,
        "umbrales": run.thresholds,
    }
```

- [ ] **Step 4: Implementar el comando**

En `src/skudo/cli.py`:

1. Registrar el subcomando junto a los demás:
   `tenant_command("profile", "perfila el espejo: particiones, cobertura y distribuciones", stores=True)`.
2. Atenderlo **antes de leer el token**, en el mismo sitio que `status`, porque no habla
   con Magento:

```python
            if args.command == "profile":
                salida = {}
                for store_id in _resolve_stores(session, tenant, args.stores):
                    run = profile_store_view(session, tenant.id, store_id)
                    salida[str(store_id)] = profile_report(session, run)
                session.commit()
                _report(salida)
                return EXIT_OK
```

- [ ] **Step 5: Verificar**

Run: `uv run pytest tests/profile tests/test_cli.py -q && uv run ruff check src tests`
Expected: PASS.

- [ ] **Step 6: Sabotaje**

Mover el `profile` **detrás** de la lectura del token y comprobar que
`test_profile_no_necesita_token` falla con `EXIT_CONFIGURATION`. Revertir.

- [ ] **Step 7: Commit**

```bash
git add src/skudo/profile/report.py src/skudo/cli.py tests/profile/test_report.py tests/test_cli.py
git commit -m "feat(s1a): comando profile, sin token porque no habla con Magento"
```

---

### Task 9: Calibración contra el catálogo real

**Files:**
- Create: `docs/superpowers/s1a-calibracion.md`
- Modify: `src/skudo/profile/partition.py` (los tres umbrales, si la medición los mueve)
- Modify: `src/skudo/profile/distribution.py` (`MIN_SHARE_NUMERICO`, íd.)

**Esta tarea necesita un espejo del catálogo real, que hoy no existe.** Todo lo verificado
hasta S0 usó 228.881 productos **sintéticos** sembrados en la instancia de desarrollo; sus
distribuciones de atributos no se parecen a las del catálogo del cliente, y calibrar contra
ellas sería calibrar contra una ficción.

**Precondición, que se pide explícitamente antes de empezar:** autorización para volcar en
**solo lectura** las tablas de catálogo y EAV de la base de producción hacia la instancia
de desarrollo (`skudo_dev_db`), que es nuestra y no comparte volumen ni puerto con nada de
producción. Nada se escribe en producción; el volcado es un `mysqldump --single-transaction
--no-create-info` de las tablas de catálogo, que es el mismo tipo de lectura ya hecho en la
verificación HTTP. **Si la autorización no llega, esta tarea se cierra declarando en el
documento que los umbrales quedan sin calibrar y por qué** — y S1a se entrega con los
valores iniciales, que son una hipótesis honesta y no una medición.

- [ ] **Step 1: Pedir la autorización y, con ella, construir el espejo real**

Volcado de solo lectura → `skudo_dev_db` → `bin/magento indexer:reindex` en la instancia de
desarrollo → `skudo attributes`, `skudo categories`, `skudo full-sync` contra una base
Postgres nueva `skudo_s1`. Registrar tiempos y RSS, como en H3.

- [ ] **Step 2: Medir lo que decide los umbrales**

Con el espejo real, y **antes** de tocar ninguna constante, medir y anotar:

1. Distribución del tamaño de los attribute sets en uso (ya se sabe: 298 en uso, 10 sets
   cubren el 45,8 %). Cuántos sets quedarían por debajo de `MIN_PARTICION` con 50, 30 y 100.
2. Para los 25 sets mayores: la ganancia del mejor divisor. Si casi todas caen por debajo
   de 0,05, el umbral está alto; si casi ninguna, está bajo.
3. Cardinalidad efectiva de los atributos `select` dentro de esos sets: cuántos quedan
   fuera con `MAX_CARD` de 12, 20 y 50.
4. Proporción de valores `ambiguo` por atributo numérico. Un atributo con muchos es un
   candidato de primera para el detector de sospecha de conversión de S1c.
5. Tiempo y memoria de la pasada completa sobre las dos store views.

- [ ] **Step 3: Ajustar los umbrales, si la medición lo pide**

Cada cambio de constante va con su medición escrita al lado, en el mismo commit. Un umbral
movido sin una cifra que lo justifique es exactamente lo que este plan prohíbe.

- [ ] **Step 4: Escribir `docs/superpowers/s1a-calibracion.md`**

Con las cinco mediciones, los valores finales, y —lo más importante— **qué se vio del
catálogo que S1b y S1c van a necesitar**: sets sin subtipo, atributos con cobertura muy
distinta entre PY y BR, atributos con muchos valores ambiguos.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/s1a-calibracion.md src/skudo/profile
git commit -m "docs(s1a): umbrales calibrados contra el catálogo real"
```

---

## Auto-revisión del plan

**Cobertura del spec de S1a.** Los cinco criterios de aceptación de S1a tienen tarea:
reproducibilidad (Task 7), declaración de subtipo o de su ausencia con razón (Tasks 5 y 8),
umbrales calibrados con la medición al lado (Task 9), perfil por store view sin promediar
(Tasks 7 y 8), y los cuatro estados sin colapsar (Tasks 2, 3 y 7).

**Huecos conocidos y deliberados.**

- El descubrimiento de subtipos es de **profundidad 1**. Las tablas admiten un padre
  (`ProfilePartition` no lo declara todavía) y la segunda profundidad se añadirá cuando la
  calibración demuestre que hace falta.
- La **clase de categoría** (técnica / comercial / de campaña) no existe en el espejo, así
  que el divisor por categoría usa la **más profunda asignada** como aproximación. Queda
  anotado como deuda: es el mismo hueco que `pendiente-s0.md` registra como L5.
- El perfilador **no lee señales** (ventas, stock). El poder discriminante que calcula es
  estructural; la demanda entra en S1b.

**Consistencia de tipos.** `ProductoPerfilado`, `Counts`, `Splitter`, `Choice`, `Lectura` y
`Stats` se definen una vez y se consumen con los mismos nombres de campo en las tareas
siguientes. `coverage` y `cobertura` conviven a propósito: el primero es el nombre de la
columna, el segundo el de la propiedad en Python.

**El plan se ejecutó antes de entregarse.** Los cuatro módulos puros —`states`,
`coverage`, `partition`, `distribution`— y sus cuatro archivos de test se extrajeron de
este documento y se corrieron tal cual: **47 tests en verde**. Los seis sabotajes se
aplicaron uno por uno y cada uno rompe lo que este plan dice que rompe.

Eso encontró dos defectos en el propio plan, ya corregidos aquí:

1. Una aserción de `test_elige_el_divisor_que_separa_las_dos_fichas` pedía `gain > 0.2`
   cuando la ganancia es exactamente 0,2.
2. **`test_un_multiselect_no_es_candidato_a_divisor` era hueco**: el atributo
   `etiquetas` no tenía valores en el catálogo de prueba, así que quedaba fuera de los
   candidatos por cardinalidad cero y no por su tipo de entrada. Admitir `multiselect`
   como divisor —el sabotaje— no rompía nada. Ahora `etiquetas` lleva dos valores reales
   y el test falla cuando debe.

El segundo es la misma clase de defecto que costó seis tests en S0, y apareció en un plan
escrito con esa lección delante. La conclusión operativa no cambia: **ejecutar, no
razonar**.

**Riesgo de test hueco.** Las tareas 3, 4, 5, 6, 7 y 8 llevan un paso de **sabotaje**
explícito con el test que debe romperse. Si un sabotaje no rompe nada, el test es hueco y
se arregla antes de seguir: es la lección más cara de S0, donde seis tests pasaban con la
conducta borrada.

---

## Traspaso a ejecución

Plan guardado. Dos formas de ejecutarlo:

**1. Por subagentes (recomendado)** — un subagente nuevo por tarea, revisión entre tareas,
iteración rápida. Es como se construyó S0, y las seis tareas con paso de sabotaje se
benefician de que quien revisa no sea quien escribió.

**2. En línea** — ejecución por lotes con puntos de control en esta sesión.

La **Task 9 es la única que necesita una decisión previa tuya**: sin autorización para leer
en modo lectura las tablas de catálogo de producción, los umbrales quedan sin calibrar y
S1a se entrega con valores que son una hipótesis declarada. Las Tasks 1 a 8 no dependen de
nada tuyo y pueden ejecutarse enteras.
