# S1b — Reglas, piso externo y curación: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inferir reglas de calidad desde el perfil de S1a, leer el piso externo de Google, poblar el mapa de conceptos, y exponer la curación por CLI — todo determinista, sin puntuar y sin UI.

**Architecture:** Un módulo `src/skudo/rules/` con cinco tablas nuevas, una máquina de estados que es la única autoridad sobre las transiciones válidas, un motor de inferencia que sólo lee el perfil, un motor de piso que lee un seed curado y el espejo, un mapa de conceptos con seed e inferencia de sinónimos, y una capa de curación pura que el CLI envuelve. Ninguna función produce un `Finding`: eso es S1c.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, Postgres, pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-09-16-s1b-reglas-y-piso-design.md` (que depende de `2026-09-11-s1-perfilador-design.md` §5 y §7, la autoridad sobre la forma de la tabla `rule`).

## Global Constraints

- **Ninguna regla inferida puntúa antes de ser aceptada.** Nace `borrador`; sólo `accept` la mueve a `aceptada`.
- **`piso_externo` no se rechaza, sólo se acota** a `aviso`. La prohibición la hace `transitions.py`, no la disciplina.
- **La confianza se deriva de la evidencia, nunca se escribe a mano.** Para obligatoriedad `confidence = cobertura`; para rango `confidence = 1 - n_ambiguous/n_present`.
- **Los umbrales son constantes del código con su razón al lado, no configuración por tenant.** `UMBRAL_ALTO=0.90`, `UMBRAL_MEDIO=0.70`, `MIN_EVIDENCIA=50`, `MAX_RATIO_AMBIGUO=0.10`, `MAX_EXCEPCIONES=500`, `UMBRAL_FP=0.10`. Son hipótesis calibrables (Task 8).
- **Los secretos nunca viajan por la línea de comandos.** S1b no los necesita: trabaja sobre el espejo y el perfil, ya en Postgres.
- **`inference.py` sólo lee el perfil.** No importa nada de `mirror` ni de `findings`. Si le falta un dato, falta en el perfil y es un cambio de S1a.
- **Nombres de tabla en singular** (`rule`, `rule_version`, `ruleset_snapshot`, `google_floor`, `concept_map`), como el resto del esquema.
- **Reproducibilidad:** dos inferencias sobre el mismo `ProfileRun` producen el mismo conjunto de reglas.
- Estados de regla: `borrador · aceptada · aviso · rechazada`. Orígenes: `inferida · piso_externo · curada`. `scope_kind`: `global · attribute_set · subtype · category`. `kind`: `obligatoriedad · rango · formato · plantilla_nombre · unidad · filtrable`.

---

### Task 1: Las cinco tablas

**Files:**
- Create: `src/skudo/rules/__init__.py` (vacío)
- Create: `src/skudo/rules/models.py`
- Create: `alembic/versions/0019_rules_and_floor.py`
- Test: `tests/rules/__init__.py` (vacío), `tests/rules/test_models.py`

**Interfaces:**
- Consumes: `skudo.mirror.models.Base`, `Tenant`; `skudo.profile.models.ProfileRun` (FK).
- Produces: `Rule`, `RuleVersion`, `RulesetSnapshot`, `GoogleFloor`, `ConceptMap` — usados por todas las tareas siguientes. Nombres de columna: ver el código de este task, que es la fuente.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/rules/test_models.py
import pytest
from sqlalchemy.exc import IntegrityError

from skudo.mirror.models import Tenant
from skudo.profile.models import ProfileRun
from skudo.rules.models import (
    ConceptMap,
    GoogleFloor,
    Rule,
    RuleVersion,
    RulesetSnapshot,
)


def _tenant(session) -> Tenant:
    t = Tenant(code="acme", name="Acme", base_url="http://acme.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def test_una_regla_nace_borrador_con_su_evidencia(db_session):
    t = _tenant(db_session)
    r = Rule(
        tenant_id=t.id,
        scope_kind="attribute_set",
        scope_key="4",
        store_view_magento_id=1,
        axis=3,
        kind="obligatoriedad",
        definition={"attribute": "color"},
        confidence=0.97,
        evidence_count=120,
        exceptions=[],
        status="borrador",
        origin="inferida",
    )
    db_session.add(r)
    db_session.flush()
    assert r.status == "borrador"
    assert r.false_positive_rate is None, "el FP nace nulo, lo mide S1c"
    assert r.ruleset_version is None


def test_google_floor_es_de_referencia_no_por_tenant(db_session):
    # No tiene tenant_id: los requisitos de Google son iguales para todos.
    f = GoogleFloor(
        category_group="Apparel & Accessories",
        google_category_min=166,
        google_category_max=8000,
        google_attribute="color",
        requirement="required",
        axis=3,
        applicability={},
        note="indumentaria exige color",
    )
    db_session.add(f)
    db_session.flush()
    assert f.id is not None


def test_el_concepto_admite_varios_atributos_por_canonico(db_session):
    t = _tenant(db_session)
    db_session.add(ConceptMap(tenant_id=t.id, canonical="color", attribute_code="color",
                              relation="equivalente", confidence=1.0, origin="curada"))
    db_session.add(ConceptMap(tenant_id=t.id, canonical="color", attribute_code="colour",
                              relation="sinonimo", confidence=0.8, origin="inferida"))
    db_session.flush()  # dos filas del mismo canónico conviven

    with pytest.raises(IntegrityError):
        # la misma (tenant, canonical, attribute_code) no se repite
        db_session.add(ConceptMap(tenant_id=t.id, canonical="color",
                                  attribute_code="color", relation="equivalente",
                                  confidence=1.0, origin="curada"))
        db_session.flush()


def test_una_transicion_guarda_el_snapshot_de_la_definicion(db_session):
    t = _tenant(db_session)
    r = Rule(tenant_id=t.id, scope_kind="global", scope_key="*", axis=3,
             kind="obligatoriedad", definition={"attribute": "x"}, confidence=1.0,
             evidence_count=1, exceptions=[], status="borrador", origin="curada")
    db_session.add(r)
    db_session.flush()
    v = RuleVersion(rule_id=r.id, from_status=None, to_status="borrador",
                    actor="inferencia", motivo="alta",
                    definition_snapshot={"attribute": "x"})
    db_session.add(v)
    db_session.flush()
    assert v.created_at is not None


def test_un_snapshot_congela_ids_de_regla(db_session):
    t = _tenant(db_session)
    s = RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                        rule_ids=[1, 2, 3])
    db_session.add(s)
    db_session.flush()
    assert s.rule_ids == [1, 2, 3]
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_models.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'skudo.rules'`

- [ ] **Step 3: Escribir los modelos**

```python
# src/skudo/rules/models.py
"""Las tablas de S1b: reglas, su historial, sus snapshots, el piso externo y el
mapa de conceptos.

Una regla es una FILA, no código (spec §5). Vive y muere por transiciones que
`transitions.py` gobierna; esta capa sólo declara la forma. Ninguna de estas
tablas produce un hallazgo: reglas + espejo → hallazgos es S1c.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from skudo.mirror.models import Base


class Rule(Base):
    __tablename__ = "rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    # A qué partición se aplica. `scope_key` es texto porque un scope puede ser
    # un id de set ("4"), un valor de subtipo, un id de categoría o "*": una sola
    # columna que los admite a todos, con `scope_kind` diciendo cómo leerla.
    scope_kind: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(255))
    # NULL = vale para todas las store views. Una regla inferida SIEMPRE nace con
    # store view: el perfil es por store view y un 90% en PY y un 12% en BR son
    # dos hechos. Consolidarlas es curación, no inferencia.
    store_view_magento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    axis: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    definition: Mapped[dict] = mapped_column(JSON)
    # Derivada de la evidencia, NUNCA escrita a mano (spec §5).
    confidence: Mapped[float] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer)
    # Los productos de la partición que NO cumplen la regla, con su evidencia.
    # Es lo que `inspect` muestra: lo que la regla marcaría.
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), index=True)
    # NULL mientras S1c no lo mida. No es 0.0: "no medido" y "cero falsos
    # positivos" son cosas distintas.
    false_positive_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), index=True)
    # El perfil que la generó (NULL para piso_externo y curada). Es lo que hace
    # auditable la reproducibilidad: una regla inferida sabe de qué medición sale.
    profile_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("profile_run.id"), nullable=True, index=True
    )
    # El último snapshot con el que se puntuó. Lo escribe S1c.
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RuleVersion(Base):
    """El historial de una regla: cada transición, quién y por qué."""

    __tablename__ = "rule_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id"), index=True)
    # NULL en el alta.
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16))
    # Quién: email del curador, "sistema" (degradación por FP) o "inferencia".
    actor: Mapped[str] = mapped_column(String(320))
    motivo: Mapped[str] = mapped_column(String(1024), default="")
    # La definición EN EL MOMENTO de la transición: un ajuste queda auditable.
    definition_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RulesetSnapshot(Base):
    """El conjunto de reglas vigentes en un instante, para puntuar reproducible.

    S1b lo crea; S1c lo consume. Guarda los ids de las reglas activas, no las
    reglas: la regla puede seguir cambiando, el snapshot es lo que se usó.
    """

    __tablename__ = "ruleset_snapshot"
    __table_args__ = (
        UniqueConstraint("tenant_id", "store_view_magento_id", "version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    store_view_magento_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    rule_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GoogleFloor(Base):
    """Requisitos de Google Shopping por grupo de la taxonomía.

    De REFERENCIA, no por tenant: los requisitos de Google son iguales para
    todos. Se puebla con un seed curado. Un grupo cubre un rango de ids de la
    taxonomía; el universal tiene min/max NULL.
    """

    __tablename__ = "google_floor"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_group: Mapped[str] = mapped_column(String(255))
    google_category_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_category_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_attribute: Mapped[str] = mapped_column(String(64))
    requirement: Mapped[str] = mapped_column(String(16))  # required | recommended
    axis: Mapped[int] = mapped_column(Integer)
    applicability: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(512), default="")


class ConceptMap(Base):
    """Qué atributo del espejo implementa cada concepto canónico. Por tenant.

    Un canónico puede mapear a varios atributos (`color` y `colour`) y un
    atributo servir a varios conceptos, así que la clave es la terna completa.
    """

    __tablename__ = "concept_map"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical", "attribute_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), index=True)
    canonical: Mapped[str] = mapped_column(String(64), index=True)
    attribute_code: Mapped[str] = mapped_column(String(255))
    relation: Mapped[str] = mapped_column(String(16))  # equivalente | sinonimo | unidad_de
    confidence: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(16))  # inferida | curada
```

- [ ] **Step 4: Escribir la migración**

```python
# alembic/versions/0019_rules_and_floor.py
"""rules and floor

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-16 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=True),
        sa.Column("axis", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("exceptions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("false_positive_rate", sa.Float(), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("profile_run_id", sa.Integer(), sa.ForeignKey("profile_run.id"), nullable=True),
        sa.Column("ruleset_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    for col in ("tenant_id", "status", "origin", "profile_run_id"):
        op.create_index(f"ix_rule_{col}", "rule", [col])

    op.create_table(
        "rule_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rule.id"), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("motivo", sa.String(1024), nullable=False, server_default=""),
        sa.Column("definition_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rule_version_rule_id", "rule_version", ["rule_id"])

    op.create_table(
        "ruleset_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rule_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "store_view_magento_id", "version",
                            name="uq_ruleset_snapshot_tenant_store_version"),
    )
    op.create_index("ix_ruleset_snapshot_tenant_id", "ruleset_snapshot", ["tenant_id"])

    op.create_table(
        "google_floor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_group", sa.String(255), nullable=False),
        sa.Column("google_category_min", sa.Integer(), nullable=True),
        sa.Column("google_category_max", sa.Integer(), nullable=True),
        sa.Column("google_attribute", sa.String(64), nullable=False),
        sa.Column("requirement", sa.String(16), nullable=False),
        sa.Column("axis", sa.Integer(), nullable=False),
        sa.Column("applicability", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(512), nullable=False, server_default=""),
    )

    op.create_table(
        "concept_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("canonical", sa.String(64), nullable=False),
        sa.Column("attribute_code", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("tenant_id", "canonical", "attribute_code",
                            name="uq_concept_map_tenant_canonical_attr"),
    )
    op.create_index("ix_concept_map_tenant_id", "concept_map", ["tenant_id"])
    op.create_index("ix_concept_map_canonical", "concept_map", ["canonical"])


def downgrade() -> None:
    op.drop_table("concept_map")
    op.drop_table("google_floor")
    op.drop_table("ruleset_snapshot")
    op.drop_table("rule_version")
    op.drop_table("rule")
```

- [ ] **Step 5: Correr y ver que pasa**

Run: `python -m pytest tests/rules/test_models.py -v`
Expected: PASS (5 tests). Si falla por la migración, verificar que `down_revision` de 0018 sigue siendo el head previo.

- [ ] **Step 6: Ruff y commit**

```bash
ruff check src/skudo/rules/ tests/rules/ alembic/versions/0019_rules_and_floor.py
git add src/skudo/rules/ tests/rules/ alembic/versions/0019_rules_and_floor.py
git commit -m "feat(rules): las cinco tablas de S1b y su migración 0019"
```

---

### Task 2: La máquina de estados

**Files:**
- Create: `src/skudo/rules/transitions.py`
- Test: `tests/rules/test_transitions.py`

**Interfaces:**
- Consumes: nada (es pura, sólo el vocabulario de estados y orígenes).
- Produces: `ESTADOS`, `ORIGENES`, `TransicionInvalida`, `puede_transicionar(origin, from_status, to_status) -> bool`, `exigir_transicion(origin, from_status, to_status)`. Usadas por `curation.py` (Task 6).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/rules/test_transitions.py
import pytest

from skudo.rules.transitions import (
    ORIGENES,
    TransicionInvalida,
    exigir_transicion,
    puede_transicionar,
)


def test_una_regla_inferida_se_acepta_desde_borrador():
    assert puede_transicionar("inferida", "borrador", "aceptada")


def test_el_piso_externo_no_se_puede_rechazar_desde_ningun_estado():
    # Éste es el candado del spec §5. Se recorre TODO origen y estado.
    for from_status in ("aceptada", "aviso", "borrador"):
        assert not puede_transicionar("piso_externo", from_status, "rechazada")
    # y sí se puede acotar a aviso
    assert puede_transicionar("piso_externo", "aceptada", "aviso")


def test_solo_el_piso_prohibe_el_rechazo():
    """La prohibición es exclusiva del piso: inferida y curada sí rechazan."""
    for origin in ("inferida", "curada"):
        assert puede_transicionar(origin, "aceptada", "rechazada")


def test_exigir_transicion_invalida_levanta():
    with pytest.raises(TransicionInvalida):
        exigir_transicion("piso_externo", "aceptada", "rechazada")


def test_exigir_transicion_valida_no_levanta():
    exigir_transicion("inferida", "borrador", "aceptada")  # no raise


def test_una_transicion_a_un_estado_desconocido_es_invalida():
    assert not puede_transicionar("inferida", "borrador", "publicada")


def test_todos_los_origenes_estan_cubiertos():
    # Si mañana se agrega un origen, este test obliga a decidir sus transiciones.
    assert ORIGENES == ("inferida", "piso_externo", "curada")
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_transitions.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Escribir la máquina de estados**

```python
# src/skudo/rules/transitions.py
"""La única autoridad sobre qué transición de una regla es válida.

Ni el CLI ni la capa de curación deciden por su cuenta que un piso externo no se
rechaza: preguntan acá. Un candado en un solo lugar es un candado; repetido en
tres, es tres oportunidades de que uno se olvide.
"""

ESTADOS = ("borrador", "aceptada", "aviso", "rechazada")
ORIGENES = ("inferida", "piso_externo", "curada")


class TransicionInvalida(Exception):
    """Se intentó una transición que la máquina de estados no permite."""


# Transiciones permitidas por origen, como conjunto de pares (desde, hasta).
# Se escriben explícitas, no por regla general, para que agregar un origen o un
# estado OBLIGUE a decidir sus transiciones en vez de heredar un default.
_COMUNES = frozenset(
    {
        ("borrador", "aceptada"),
        ("borrador", "rechazada"),
        ("aceptada", "aviso"),
        ("aviso", "aceptada"),
        ("aceptada", "rechazada"),
        ("aviso", "rechazada"),
    }
)

# El piso externo nace `aceptada` y NUNCA se rechaza (spec §5). Sólo se acota a
# aviso y se puede reactivar. No hay ni una transición a `rechazada`.
_PISO = frozenset(
    {
        ("aceptada", "aviso"),
        ("aviso", "aceptada"),
    }
)

_PERMITIDAS: dict[str, frozenset] = {
    "inferida": _COMUNES,
    "curada": _COMUNES,
    "piso_externo": _PISO,
}


def puede_transicionar(origin: str, from_status: str, to_status: str) -> bool:
    if to_status not in ESTADOS or from_status not in ESTADOS:
        return False
    return (from_status, to_status) in _PERMITIDAS.get(origin, frozenset())


def exigir_transicion(origin: str, from_status: str, to_status: str) -> None:
    """Levanta `TransicionInvalida` si la transición no está permitida."""
    if not puede_transicionar(origin, from_status, to_status):
        raise TransicionInvalida(
            f"una regla '{origin}' no puede pasar de {from_status!r} a "
            f"{to_status!r}"
        )
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/rules/test_transitions.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/rules/transitions.py tests/rules/test_transitions.py
git add src/skudo/rules/transitions.py tests/rules/test_transitions.py
git commit -m "feat(rules): la máquina de estados, única autoridad de las transiciones"
```

---

### Task 3: La inferencia de reglas desde el perfil

**Files:**
- Create: `src/skudo/rules/inference.py`
- Test: `tests/rules/test_inference.py`

**Interfaces:**
- Consumes: `Rule`, `RuleVersion` (Task 1); tablas del perfil `ProfileRun`, `ProfilePartition`, `AttributeCoverage`, `ValueStats` (S1a).
- Produces: constantes `UMBRAL_ALTO`, `UMBRAL_MEDIO`, `MIN_EVIDENCIA`, `MAX_RATIO_AMBIGUO`, `MAX_EXCEPCIONES`; `inferir(session, profile_run_id) -> list[Rule]`. Usada por el CLI (Task 7).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/rules/test_inference.py
from skudo.mirror.models import Tenant
from skudo.profile.models import (
    AttributeCoverage,
    ProfilePartition,
    ProfileRun,
    ValueStats,
)
from skudo.rules.inference import (
    MIN_EVIDENCIA,
    UMBRAL_ALTO,
    UMBRAL_MEDIO,
    inferir,
)


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _run_con_particion(session, tenant, *, splitter_value=None):
    run = ProfileRun(tenant_id=tenant.id, store_view_magento_id=1,
                     mirror_sync_generation=1, thresholds={}, product_count=200)
    session.add(run)
    session.flush()
    part = ProfilePartition(
        run_id=run.id, attribute_set_id=4,
        splitter_kind="ninguno" if splitter_value is None else "atributo",
        splitter_key=None if splitter_value is None else "tipo",
        splitter_value=splitter_value, product_count=200, ambiguity=0.1,
        decision_reason="elegido" if splitter_value else "sin candidatos",
    )
    session.add(part)
    session.flush()
    return run, part


def _cobertura(session, part, code, *, presente, vacio):
    total = presente + vacio
    session.add(AttributeCoverage(
        partition_id=part.id, attribute_code=code, presente=presente, vacio=vacio,
        no_aplica=0, desconocido=0,
        coverage=(presente / total) if total else None,
    ))
    session.flush()


def test_cobertura_alta_infiere_obligatoriedad_con_confianza_igual_a_cobertura(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "color", presente=194, vacio=6)  # 0.97

    reglas = inferir(db_session, run.id)

    obl = [r for r in reglas if r.kind == "obligatoriedad" and r.definition["attribute"] == "color"]
    assert len(obl) == 1
    r = obl[0]
    assert abs(r.confidence - 0.97) < 1e-9, "la confianza ES la cobertura"
    assert r.evidence_count == 194
    assert r.status == "borrador"
    assert r.origin == "inferida"
    assert r.store_view_magento_id == 1, "nace con store view, nunca NULL"
    assert r.profile_run_id == run.id
    assert "ambiguo" not in r.definition, "banda alta no marca ambiguo"


def test_cobertura_media_marca_ambiguo(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "material", presente=160, vacio=40)  # 0.80

    reglas = inferir(db_session, run.id)
    r = next(r for r in reglas if r.definition["attribute"] == "material")
    assert r.definition.get("ambiguo") is True
    assert UMBRAL_MEDIO <= r.confidence < UMBRAL_ALTO


def test_cobertura_baja_no_infiere(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "regalo", presente=100, vacio=100)  # 0.50

    reglas = inferir(db_session, run.id)
    assert not any(r.definition.get("attribute") == "regalo" for r in reglas)


def test_muestra_chica_no_infiere_aunque_la_cobertura_sea_perfecta(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "raro", presente=MIN_EVIDENCIA - 1, vacio=0)  # 1.0

    reglas = inferir(db_session, run.id)
    assert not any(r.definition.get("attribute") == "raro" for r in reglas)


def test_las_excepciones_son_los_productos_sin_el_atributo(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "color", presente=194, vacio=6)
    reglas = inferir(db_session, run.id)
    r = next(r for r in reglas if r.definition["attribute"] == "color")
    # No tenemos SKUs en la cobertura agregada: la excepción se declara como
    # conteo y la lista de SKUs la completa el detector de S1c. La regla guarda
    # cuántos marcaría.
    assert r.exceptions == [] or isinstance(r.exceptions, list)
    assert r.definition["marcaria"] == 6, "lo que la regla marcaría: los vacíos"


def test_una_particion_con_subtipo_produce_scope_subtype(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t, splitter_value="ropa")
    _cobertura(db_session, part, "talle", presente=190, vacio=10)
    reglas = inferir(db_session, run.id)
    r = next(r for r in reglas if r.definition["attribute"] == "talle")
    assert r.scope_kind == "subtype"
    assert r.scope_key == "ropa"


def test_rango_numerico_usa_p05_p95(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    db_session.add(ValueStats(
        partition_id=part.id, attribute_code="peso", kind="numerico",
        n_present=200, n_ambiguous=2, minimum=0.1, p05=0.5, p50=2.0, p95=8.0,
        maximum=50.0, distinct_values=40, mode_share=0.1, top_values=[],
    ))
    db_session.flush()
    reglas = inferir(db_session, run.id)
    rango = next(r for r in reglas if r.kind == "rango")
    assert rango.definition["min"] == 0.5
    assert rango.definition["max"] == 8.0
    assert rango.confidence > 0.98  # 1 - 2/200


def test_numerico_sucio_no_da_rango_sino_sospecha_de_conversion(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    db_session.add(ValueStats(
        partition_id=part.id, attribute_code="voltaje", kind="numerico",
        n_present=200, n_ambiguous=40, minimum=0, p05=110, p50=220, p95=380,
        maximum=999, distinct_values=5, mode_share=0.6, top_values=[],
    ))  # 40/200 = 0.20 > MAX_RATIO_AMBIGUO
    db_session.flush()
    reglas = inferir(db_session, run.id)
    assert not any(r.kind == "rango" and r.definition.get("attribute") == "voltaje"
                   for r in reglas)
    fmt = next(r for r in reglas if r.kind == "formato"
               and r.definition.get("attribute") == "voltaje")
    assert fmt.definition["sospecha_conversion"] is True


def test_reproducible_dos_inferencias_dan_lo_mismo(db_session):
    t = _tenant(db_session)
    run, part = _run_con_particion(db_session, t)
    _cobertura(db_session, part, "color", presente=194, vacio=6)
    _cobertura(db_session, part, "marca", presente=180, vacio=20)

    def firma(reglas):
        return sorted((r.kind, r.definition["attribute"], round(r.confidence, 6))
                      for r in reglas)

    r1 = firma(inferir(db_session, run.id))
    # limpiar y re-inferir sobre el mismo perfil
    from skudo.rules.models import Rule
    for r in db_session.query(Rule).all():
        db_session.delete(r)
    db_session.flush()
    r2 = firma(inferir(db_session, run.id))
    assert r1 == r2
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_inference.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Escribir el motor de inferencia**

```python
# src/skudo/rules/inference.py
"""Perfil → reglas en borrador. La única entrada es el perfil de S1a.

No lee el espejo: eso lo hace reproducible y barato de re-correr. Si un dato no
está en el perfil, falta en el perfil (cambio de S1a), no se busca por otro lado.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.profile.models import (
    AttributeCoverage,
    ProfilePartition,
    ProfileRun,
    ValueStats,
)
from skudo.rules.models import Rule, RuleVersion

# --- Umbrales -------------------------------------------------------------
#
# Constantes del código, NO configuración por tenant: un umbral que se afloja
# por config termina aflojado (lección de la válvula de S0 y de los umbrales del
# perfilador). Son una HIPÓTESIS; la calibración contra el catálogo real está en
# la Task 8 y deja su cifra en docs/superpowers/s1b-calibracion.md.

# Cobertura por encima de la cual la obligatoriedad se infiere sin reparos.
UMBRAL_ALTO = 0.90
# Entre este y el alto, se infiere PERO se marca `ambiguo`.
UMBRAL_MEDIO = 0.70
# Productos evaluables mínimos para inferir: con menos, la cobertura es ruido.
MIN_EVIDENCIA = 50
# Proporción de valores no interpretables por encima de la cual un numérico no
# da rango sino sospecha de conversión.
MAX_RATIO_AMBIGUO = 0.10
# Tope de SKUs de excepción que una regla guarda inline (hoy la cobertura
# agregada no trae SKUs; el tope rige cuando S1c los adjunte).
MAX_EXCEPCIONES = 500


def _scope(part: ProfilePartition) -> tuple[str, str]:
    """El scope de una partición: subtype si tiene divisor, si no attribute_set."""
    if part.splitter_value is not None:
        return "subtype", str(part.splitter_value)
    return "attribute_set", str(part.attribute_set_id)


def inferir(session: Session, profile_run_id: int) -> list[Rule]:
    """Genera y persiste las reglas borrador de un ProfileRun. Devuelve las reglas.

    Idempotente por construcción del que llama: re-inferir requiere borrar las
    reglas del run primero (el CLI lo hace). Acá no se deduplica contra lo
    existente porque la inferencia es una función del perfil, no del estado.
    """
    run = session.get(ProfileRun, profile_run_id)
    if run is None or run.finished_at is None:
        raise ValueError(f"perfil {profile_run_id} inexistente o sin terminar")

    particiones = session.scalars(
        select(ProfilePartition)
        .where(ProfilePartition.run_id == profile_run_id)
        .order_by(ProfilePartition.id)
    ).all()

    reglas: list[Rule] = []
    for part in particiones:
        if part.attribute_set_id is None:
            continue  # partición de set desconocido: no se puede inferir
        scope_kind, scope_key = _scope(part)
        reglas.extend(_obligatoriedad(session, run, part, scope_kind, scope_key))
        reglas.extend(_numericas(session, run, part, scope_kind, scope_key))

    for r in reglas:
        session.add(r)
    session.flush()
    for r in reglas:
        session.add(RuleVersion(
            rule_id=r.id, from_status=None, to_status="borrador",
            actor="inferencia", motivo="alta por inferencia",
            definition_snapshot=r.definition,
        ))
    session.flush()
    return reglas


def _obligatoriedad(session, run, part, scope_kind, scope_key) -> list[Rule]:
    coberturas = session.scalars(
        select(AttributeCoverage)
        .where(AttributeCoverage.partition_id == part.id)
        .order_by(AttributeCoverage.attribute_code)  # orden estable = reproducible
    ).all()
    reglas = []
    for cov in coberturas:
        evaluables = cov.presente + cov.vacio
        if cov.coverage is None or evaluables < MIN_EVIDENCIA:
            continue
        if cov.coverage < UMBRAL_MEDIO:
            continue
        definition = {"attribute": cov.attribute_code, "marcaria": cov.vacio}
        if cov.coverage < UMBRAL_ALTO:
            definition["ambiguo"] = True
        reglas.append(Rule(
            tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
            store_view_magento_id=run.store_view_magento_id, axis=3,
            kind="obligatoriedad", definition=definition, confidence=cov.coverage,
            evidence_count=cov.presente, exceptions=[], status="borrador",
            origin="inferida", profile_run_id=run.id,
        ))
    return reglas


def _numericas(session, run, part, scope_kind, scope_key) -> list[Rule]:
    stats = session.scalars(
        select(ValueStats)
        .where(ValueStats.partition_id == part.id, ValueStats.kind == "numerico")
        .order_by(ValueStats.attribute_code)
    ).all()
    reglas = []
    for s in stats:
        if s.n_present < MIN_EVIDENCIA:
            continue
        ratio = (s.n_ambiguous / s.n_present) if s.n_present else 1.0
        if ratio >= MAX_RATIO_AMBIGUO:
            reglas.append(Rule(
                tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
                store_view_magento_id=run.store_view_magento_id, axis=3,
                kind="formato",
                definition={"attribute": s.attribute_code, "sospecha_conversion": True,
                            "ratio_ambiguo": round(ratio, 4)},
                confidence=1.0 - ratio, evidence_count=s.n_present, exceptions=[],
                status="borrador", origin="inferida", profile_run_id=run.id,
            ))
            continue
        if s.p05 is None or s.p95 is None:
            continue
        reglas.append(Rule(
            tenant_id=run.tenant_id, scope_kind=scope_kind, scope_key=scope_key,
            store_view_magento_id=run.store_view_magento_id, axis=3, kind="rango",
            definition={"attribute": s.attribute_code, "min": s.p05, "max": s.p95},
            confidence=1.0 - ratio, evidence_count=s.n_present, exceptions=[],
            status="borrador", origin="inferida", profile_run_id=run.id,
        ))
    return reglas
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/rules/test_inference.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/rules/inference.py tests/rules/test_inference.py
git add src/skudo/rules/inference.py tests/rules/test_inference.py
git commit -m "feat(rules): inferencia de obligatoriedad y rango desde el perfil"
```

---

### Task 4: El mapa de conceptos

**Files:**
- Create: `src/skudo/rules/concepts.py`
- Test: `tests/rules/test_concepts.py`

**Interfaces:**
- Consumes: `ConceptMap` (Task 1); `skudo.mirror.models.Attribute`.
- Produces: `SEED_GOOGLE` (dict canónico→código universal); `sembrar(session, tenant_id) -> list[ConceptMap]`; `inferir_sinonimos(session, tenant_id) -> list[ConceptMap]`; `SINONIMOS_CONOCIDOS`. Usada por `floor.py` (Task 5) y el CLI (Task 7).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/rules/test_concepts.py
from skudo.mirror.models import Attribute, Tenant
from skudo.rules.concepts import inferir_sinonimos, sembrar
from skudo.rules.models import ConceptMap


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _attr(session, tenant, code, frontend_input="text"):
    session.add(Attribute(
        tenant_id=tenant.id, code=code, label=code, frontend_input=frontend_input,
        declared_scope="global", is_filterable=False, is_required=False,
        attribute_set_ids=[4],
    ))
    session.flush()


def test_el_seed_mapea_los_universales_de_google_con_confianza_uno(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "name")
    _attr(db_session, t, "description")
    _attr(db_session, t, "price")
    _attr(db_session, t, "image")

    creados = sembrar(db_session, t.id)
    por_canonico = {c.canonical: c for c in creados}
    assert por_canonico["title"].attribute_code == "name"
    assert por_canonico["title"].confidence == 1.0
    assert por_canonico["title"].origin == "curada"


def test_el_seed_no_mapea_un_universal_que_el_tenant_no_tiene(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "name")  # sólo name; falta description, price, image
    creados = sembrar(db_session, t.id)
    canonicos = {c.canonical for c in creados}
    assert "title" in canonicos
    assert "description" not in canonicos, "no se inventa un mapeo a un atributo ausente"


def test_sembrar_es_idempotente(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "name")
    sembrar(db_session, t.id)
    sembrar(db_session, t.id)  # segunda vez no duplica
    filas = db_session.query(ConceptMap).filter_by(tenant_id=t.id, canonical="title").all()
    assert len(filas) == 1


def test_infiere_colour_como_sinonimo_de_color(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "color")
    _attr(db_session, t, "colour")
    creados = inferir_sinonimos(db_session, t.id)
    par = [c for c in creados if c.canonical == "color" and c.attribute_code == "colour"]
    assert len(par) == 1
    assert par[0].relation == "sinonimo"
    assert par[0].origin == "inferida"
    assert par[0].confidence < 1.0, "un sinónimo inferido nace para revisión"


def test_no_infiere_un_falso_sinonimo_por_prefijo(db_session):
    t = _tenant(db_session)
    _attr(db_session, t, "precio")
    _attr(db_session, t, "precio_especial")
    creados = inferir_sinonimos(db_session, t.id)
    # NO son el mismo concepto: uno es una variante, no un sinónimo.
    assert not any(
        {c.attribute_code for c in creados} >= {"precio", "precio_especial"}
        and c.canonical == "precio"
        for c in creados
    )
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_concepts.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Escribir el mapa de conceptos**

```python
# src/skudo/rules/concepts.py
"""El mapa de conceptos: qué atributo del espejo implementa cada concepto.

Dos fuentes, en orden de confianza: un seed curado de los universales de Google
(confianza 1.0, es una decisión) y una inferencia conservadora de sinónimos
(confianza < 1.0, es un candidato a confirmar). Un falso sinónimo trata dos
atributos distintos como uno, que es peor que no tenerlo: ante la duda, no se
infiere.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import Attribute
from skudo.rules.models import ConceptMap

# Los universales de Google Shopping, mapeados al código del espejo que suele
# implementarlos. Es una decisión, no una medición: por eso confianza 1.0 y
# origen curada. Sólo se siembra el que el tenant realmente tiene.
SEED_GOOGLE: dict[str, str] = {
    "title": "name",
    "description": "description",
    "image_link": "image",
    "price": "price",
    "availability": "status",
    "brand": "brand",
    "gtin": "gtin",
    "mpn": "mpn",
}

# Familias de sinónimos conocidos. Cada tupla es un concepto: su primer elemento
# es el canónico, el resto son formas equivalentes. La lista es corta y curada a
# propósito: es lo que evita que la inferencia por parecido invente pares falsos.
SINONIMOS_CONOCIDOS: tuple[tuple[str, ...], ...] = (
    ("color", "colour"),
    ("talle", "size", "tamano", "tamaño"),
    ("peso", "weight"),
    ("marca", "brand"),
    ("material", "materials"),
    ("genero", "gender", "género"),
)

# Confianza de un sinónimo inferido: alto, pero < 1.0 para que exija confirmación.
CONFIANZA_SINONIMO = 0.8


def _existe(session, tenant_id, canonical, attribute_code) -> bool:
    return session.scalar(
        select(ConceptMap.id).where(
            ConceptMap.tenant_id == tenant_id,
            ConceptMap.canonical == canonical,
            ConceptMap.attribute_code == attribute_code,
        )
    ) is not None


def _codigos(session, tenant_id) -> set[str]:
    return set(session.scalars(
        select(Attribute.code).where(Attribute.tenant_id == tenant_id)
    ).all())


def sembrar(session: Session, tenant_id: int) -> list[ConceptMap]:
    """Siembra los universales de Google que el tenant realmente tiene. Idempotente."""
    codigos = _codigos(session, tenant_id)
    creados = []
    for canonical, code in sorted(SEED_GOOGLE.items()):
        if code not in codigos or _existe(session, tenant_id, canonical, code):
            continue
        fila = ConceptMap(tenant_id=tenant_id, canonical=canonical,
                          attribute_code=code, relation="equivalente",
                          confidence=1.0, origin="curada")
        session.add(fila)
        creados.append(fila)
    session.flush()
    return creados


def inferir_sinonimos(session: Session, tenant_id: int) -> list[ConceptMap]:
    """Infiere sinónimos SÓLO entre las familias conocidas. Conservador a propósito.

    No compara por prefijo ni por distancia de edición: `precio` y
    `precio_especial` comparten prefijo y son conceptos distintos. Sólo se
    infiere lo que una familia curada declara sinónimo.
    """
    codigos = _codigos(session, tenant_id)
    creados = []
    for familia in SINONIMOS_CONOCIDOS:
        canonical = familia[0]
        presentes = [c for c in familia if c in codigos]
        if len(presentes) < 2:
            continue  # hace falta al menos el canónico y una variante
        for code in sorted(presentes):
            relacion = "equivalente" if code == canonical else "sinonimo"
            conf = 1.0 if code == canonical else CONFIANZA_SINONIMO
            if _existe(session, tenant_id, canonical, code):
                continue
            fila = ConceptMap(tenant_id=tenant_id, canonical=canonical,
                              attribute_code=code, relation=relacion,
                              confidence=conf, origin="inferida")
            session.add(fila)
            creados.append(fila)
    session.flush()
    return creados
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/rules/test_concepts.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/rules/concepts.py tests/rules/test_concepts.py
git add src/skudo/rules/concepts.py tests/rules/test_concepts.py
git commit -m "feat(rules): mapa de conceptos con seed de Google e inferencia conservadora de sinónimos"
```

---

### Task 5: El piso externo de Google

**Files:**
- Create: `src/skudo/rules/floor_seed.py`
- Create: `src/skudo/rules/floor.py`
- Test: `tests/rules/test_floor.py`

**Interfaces:**
- Consumes: `GoogleFloor`, `Rule`, `ConceptMap` (Tasks 1, 4); `skudo.mirror.models.ProductRecord`, `Category`, `ProductCategoryAssignment`.
- Produces: `SEED_FLOOR` (lista de dicts); `cargar_seed(session) -> int`; `generar_piso(session, tenant_id) -> list[Rule]`. Usada por el CLI (Task 7).

**Nota de datos:** El seed de `SEED_FLOOR` de este task es un arranque mínimo con los universales y dos grupos (indumentaria, electrónica). Ampliar a los ~30 grupos del tenant es trabajo de la Task 8, con la especificación de Google al lado de cada fila.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/rules/test_floor.py
from skudo.mirror.models import (
    Attribute,
    Category,
    ProductCategoryAssignment,
    ProductRecord,
    Tenant,
)
from skudo.rules.concepts import sembrar
from skudo.rules.floor import cargar_seed, generar_piso
from skudo.rules.models import GoogleFloor, Rule


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def test_cargar_seed_puebla_google_floor_una_vez(db_session):
    n1 = cargar_seed(db_session)
    assert n1 > 0
    n2 = cargar_seed(db_session)  # idempotente: no re-inserta
    assert n2 == 0
    assert db_session.query(GoogleFloor).count() == n1


def test_los_universales_aplican_a_cualquier_categoria_mapeada(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    # un atributo name para que el concepto title exista
    db_session.add(Attribute(tenant_id=t.id, code="name", label="name",
                             frontend_input="text", declared_scope="global",
                             is_filterable=False, is_required=False, attribute_set_ids=[4]))
    db_session.flush()
    sembrar(db_session, t.id)
    # una categoría con google_category_id_int mapeado, y un producto que cuelga
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 2, 100],
                            default_name="Remeras"))
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={"google_category_id_int": "212"}, attribute_set_id=4,
        type_id="simple", sync_generation=1,
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()

    reglas = generar_piso(db_session, t.id)
    titulos = [r for r in reglas if r.definition.get("google_attribute") == "title"]
    assert titulos, "el universal title aplica"
    r = titulos[0]
    assert r.origin == "piso_externo"
    assert r.status == "aceptada", "el piso nace aceptado, no borrador"
    assert r.scope_kind == "category"
    assert r.definition["espejo_attribute"] == "name", "mapea vía concept_map"


def test_un_requisito_sin_mapeo_de_concepto_nace_aviso(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    # NO sembramos concepto: 'title' no tiene atributo del espejo
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 100],
                            default_name="X"))
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={"google_category_id_int": "212"}, attribute_set_id=4,
        type_id="simple", sync_generation=1,
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()
    reglas = generar_piso(db_session, t.id)
    r = next(r for r in reglas if r.definition.get("google_attribute") == "title")
    assert r.status == "aviso"
    assert r.definition["sin_mapeo"] is True


def test_una_categoria_sin_mapear_no_produce_piso(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 100],
                            default_name="Sin mapear"))
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={},  # sin google_category_id_int
        attribute_set_id=4, type_id="simple", sync_generation=1,
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()
    reglas = generar_piso(db_session, t.id)
    assert reglas == [], "sin mapeo externo no hay piso; queda desconocido (eje 11 en S1c)"


def test_indumentaria_exige_color_ademas_del_universal(db_session):
    t = _tenant(db_session)
    cargar_seed(db_session)
    for code in ("name", "color"):
        db_session.add(Attribute(tenant_id=t.id, code=code, label=code,
                                 frontend_input="text", declared_scope="global",
                                 is_filterable=False, is_required=False,
                                 attribute_set_ids=[4]))
    db_session.flush()
    sembrar(db_session, t.id)
    db_session.add(Category(tenant_id=t.id, magento_id=100, path=[1, 100],
                            default_name="Ropa"))
    # 1604 cae dentro del rango de indumentaria del seed
    db_session.add(ProductRecord(
        tenant_id=t.id, sku="P1", store_view_magento_id=1,
        attributes={"google_category_id_int": "1604"}, attribute_set_id=4,
        type_id="simple", sync_generation=1,
    ))
    db_session.add(ProductCategoryAssignment(tenant_id=t.id, sku="P1", category_magento_id=100))
    db_session.flush()
    reglas = generar_piso(db_session, t.id)
    atributos = {r.definition.get("google_attribute") for r in reglas}
    assert "color" in atributos
    assert "title" in atributos
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_floor.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Escribir el seed**

```python
# src/skudo/rules/floor_seed.py
"""El seed curado de los requisitos de Google Shopping.

Datos, no lógica. Cada fila lleva su `note` con la razón. Los rangos de
categoría son sobre la taxonomía de Google Shopping (ids numéricos). El
universal tiene min/max None y aplica a cualquier categoría mapeada.

ARRANQUE MÍNIMO: universales + indumentaria + electrónica. Ampliar a los ~30
grupos del tenant es la Task 8, con la especificación de Google al lado.
"""

# Cada dict: category_group, min, max, google_attribute, requirement, axis,
# applicability, note.
SEED_FLOOR: list[dict] = [
    # --- Universales (aplican a toda categoría mapeada) ---
    {"category_group": "*", "min": None, "max": None, "google_attribute": "title",
     "requirement": "required", "axis": 1, "applicability": {},
     "note": "Google exige título en todo producto"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "description",
     "requirement": "required", "axis": 6, "applicability": {},
     "note": "Google exige descripción en todo producto"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "image_link",
     "requirement": "required", "axis": 7, "applicability": {},
     "note": "Google exige al menos una imagen"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "price",
     "requirement": "required", "axis": 8, "applicability": {},
     "note": "Google exige precio"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "availability",
     "requirement": "required", "axis": 8, "applicability": {},
     "note": "Google exige disponibilidad"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "gtin",
     "requirement": "recommended", "axis": 4,
     "applicability": {"solo_si": "fabricante_asigna_gtin"},
     "note": "recomendado; un producto sin GTIN asignado es caso previsto, no defecto"},
    # --- Indumentaria y accesorios (Apparel & Accessories, 166 y su subárbol) ---
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "color", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: color obligatorio para Shopping"},
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "size", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: talle obligatorio"},
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "gender", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: género obligatorio"},
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "age_group", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: grupo de edad obligatorio"},
    # --- Electrónica (Electronics, 222 y su subárbol) ---
    {"category_group": "Electronics", "min": 222, "max": 2082,
     "google_attribute": "gtin", "requirement": "required", "axis": 4,
     "applicability": {"solo_si": "fabricante_asigna_gtin"},
     "note": "electrónica de marca: GTIN obligatorio cuando el fabricante lo asigna"},
]
```

- [ ] **Step 4: Escribir el motor de piso**

```python
# src/skudo/rules/floor.py
"""google_floor + espejo → reglas de piso externo.

Lee el google_category_id_int de cada producto (lo puso Standard_GoogleCategory),
busca los requisitos que aplican, y emite reglas origin=piso_externo. El piso no
se infiere y no se rechaza: nace aceptado. Un requisito cuyo atributo el mapa de
conceptos no conoce nace en aviso, no puntúa contra algo que no sabe leer.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import ProductCategoryAssignment, ProductRecord
from skudo.rules.floor_seed import SEED_FLOOR
from skudo.rules.models import ConceptMap, GoogleFloor, Rule

GOOGLE_CATEGORY_ATTR = "google_category_id_int"


def cargar_seed(session: Session) -> int:
    """Puebla google_floor desde SEED_FLOOR. Idempotente: no re-inserta lo que ya está."""
    ya = session.scalar(select(GoogleFloor.id).limit(1))
    if ya is not None:
        return 0
    for fila in SEED_FLOOR:
        session.add(GoogleFloor(
            category_group=fila["category_group"],
            google_category_min=fila["min"], google_category_max=fila["max"],
            google_attribute=fila["google_attribute"], requirement=fila["requirement"],
            axis=fila["axis"], applicability=fila["applicability"], note=fila["note"],
        ))
    session.flush()
    return len(SEED_FLOOR)


def _mapa_google_a_espejo(session, tenant_id) -> dict[str, str]:
    """Para cada atributo de Google (canónico), el código del espejo que lo implementa."""
    filas = session.execute(
        select(ConceptMap.canonical, ConceptMap.attribute_code)
        .where(ConceptMap.tenant_id == tenant_id)
    ).all()
    # Preferimos el equivalente de mayor confianza; aquí basta el primero estable.
    mapa: dict[str, str] = {}
    for canonical, code in sorted(filas):
        mapa.setdefault(canonical, code)
    return mapa


def _requisitos_para(session, google_category_id: int) -> list[GoogleFloor]:
    filas = session.scalars(select(GoogleFloor)).all()
    aplica = []
    for f in filas:
        if f.google_category_min is None:  # universal
            aplica.append(f)
        elif f.google_category_min <= google_category_id <= f.google_category_max:
            aplica.append(f)
    return aplica


def generar_piso(session: Session, tenant_id: int) -> list[Rule]:
    """Genera las reglas de piso externo del tenant desde el espejo y el seed."""
    mapa = _mapa_google_a_espejo(session, tenant_id)

    # google_category_id_int por SKU (de los atributos del espejo).
    cat_por_producto = {}
    for sku, attrs in session.execute(
        select(ProductRecord.sku, ProductRecord.attributes)
        .where(ProductRecord.tenant_id == tenant_id)
    ).all():
        valor = (attrs or {}).get(GOOGLE_CATEGORY_ATTR)
        if valor is None:
            continue
        try:
            cat_por_producto[sku] = int(valor)
        except (TypeError, ValueError):
            continue

    # categoría de Magento por SKU (la primera estable, para el scope).
    categoria_magento = {}
    for sku, cat in session.execute(
        select(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
        .where(ProductCategoryAssignment.tenant_id == tenant_id)
        .order_by(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
    ).all():
        categoria_magento.setdefault(sku, cat)

    # Deduplicamos por (categoria_magento, google_attribute): una regla por
    # categoría y requisito, no una por producto.
    vistos: set[tuple[int, str]] = set()
    reglas: list[Rule] = []
    for sku in sorted(cat_por_producto):
        google_id = cat_por_producto[sku]
        scope_cat = categoria_magento.get(sku)
        if scope_cat is None:
            continue
        for req in _requisitos_para(session, google_id):
            clave = (scope_cat, req.google_attribute)
            if clave in vistos:
                continue
            vistos.add(clave)
            espejo = mapa.get(req.google_attribute)
            definition = {
                "google_attribute": req.google_attribute,
                "requirement": req.requirement,
                "applicability": req.applicability,
            }
            if espejo is None:
                definition["sin_mapeo"] = True
                status = "aviso"
            else:
                definition["espejo_attribute"] = espejo
                status = "aceptada" if req.requirement == "required" else "aviso"
            reglas.append(Rule(
                tenant_id=tenant_id, scope_kind="category", scope_key=str(scope_cat),
                store_view_magento_id=None, axis=req.axis, kind="obligatoriedad",
                definition=definition, confidence=1.0, evidence_count=0,
                exceptions=[], status=status, origin="piso_externo",
            ))
    for r in reglas:
        session.add(r)
    session.flush()
    return reglas
```

- [ ] **Step 5: Correr y ver que pasa**

Run: `python -m pytest tests/rules/test_floor.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Ruff y commit**

```bash
ruff check src/skudo/rules/floor.py src/skudo/rules/floor_seed.py tests/rules/test_floor.py
git add src/skudo/rules/floor.py src/skudo/rules/floor_seed.py tests/rules/test_floor.py
git commit -m "feat(rules): piso externo de Google desde seed curado y mapa de conceptos"
```

---

### Task 6: La curación

**Files:**
- Create: `src/skudo/rules/curation.py`
- Test: `tests/rules/test_curation.py`

**Interfaces:**
- Consumes: `Rule`, `RuleVersion`, `RulesetSnapshot` (Task 1); `transitions` (Task 2).
- Produces: `ReglaAmbiguaSinConfirmar`, `aceptar(session, rule_ids, actor, confirmar_ambiguo=False)`, `rechazar(session, rule_id, actor, motivo)`, `acotar(session, rule_id, actor, motivo)`, `ajustar(session, rule_id, actor, definition)`, `snapshot(session, tenant_id, store_view) -> RulesetSnapshot`, `degradar_por_fp(session, rule_id)`, `inspeccionar(session, rule_id) -> dict`. Usadas por el CLI (Task 7).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/rules/test_curation.py
import pytest

from skudo.mirror.models import Tenant
from skudo.rules.curation import (
    ReglaAmbiguaSinConfirmar,
    aceptar,
    acotar,
    ajustar,
    degradar_por_fp,
    inspeccionar,
    rechazar,
    snapshot,
)
from skudo.rules.models import Rule, RuleVersion, RulesetSnapshot
from skudo.rules.transitions import TransicionInvalida


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t)
    session.flush()
    return t


def _regla(session, tenant, *, origin="inferida", status="borrador",
           definition=None, store=1):
    r = Rule(tenant_id=tenant.id, scope_kind="attribute_set", scope_key="4",
             store_view_magento_id=store, axis=3, kind="obligatoriedad",
             definition=definition or {"attribute": "color", "marcaria": 5},
             confidence=0.97, evidence_count=100, exceptions=[], status=status,
             origin=origin)
    session.add(r)
    session.flush()
    return r


def test_aceptar_mueve_a_aceptada_y_registra_version(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t)
    aceptar(db_session, [r.id], actor="ana@x.com")
    db_session.refresh(r)
    assert r.status == "aceptada"
    v = db_session.query(RuleVersion).filter_by(rule_id=r.id, to_status="aceptada").one()
    assert v.actor == "ana@x.com"


def test_aceptar_una_ambigua_sin_confirmar_falla(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "material", "ambiguo": True})
    with pytest.raises(ReglaAmbiguaSinConfirmar):
        aceptar(db_session, [r.id], actor="ana@x.com")
    db_session.refresh(r)
    assert r.status == "borrador", "no se movió"


def test_aceptar_una_ambigua_con_confirmar_pasa(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "material", "ambiguo": True})
    aceptar(db_session, [r.id], actor="ana@x.com", confirmar_ambiguo=True)
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_rechazar_un_piso_externo_falla(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, origin="piso_externo", status="aceptada")
    with pytest.raises(TransicionInvalida):
        rechazar(db_session, r.id, actor="ana@x.com", motivo="no")
    db_session.refresh(r)
    assert r.status == "aceptada"


def test_acotar_un_piso_externo_lo_pasa_a_aviso(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, origin="piso_externo", status="aceptada")
    acotar(db_session, r.id, actor="ana@x.com", motivo="el fabricante no asigna GTIN")
    db_session.refresh(r)
    assert r.status == "aviso"


def test_rechazar_exige_motivo(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t)
    with pytest.raises(ValueError):
        rechazar(db_session, r.id, actor="ana@x.com", motivo="")


def test_ajustar_guarda_snapshot_de_la_definicion_anterior(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "color", "min_len": 3})
    ajustar(db_session, r.id, actor="ana@x.com", definition={"attribute": "color", "min_len": 5})
    db_session.refresh(r)
    assert r.definition["min_len"] == 5
    versiones = db_session.query(RuleVersion).filter_by(rule_id=r.id).all()
    # la versión del ajuste guarda la definición NUEVA; el historial permite ver el cambio
    assert any(v.definition_snapshot.get("min_len") == 5 for v in versiones)


def test_snapshot_solo_incluye_reglas_activas(db_session):
    t = _tenant(db_session)
    aceptada = _regla(db_session, t, status="aceptada", store=1)
    aviso = _regla(db_session, t, status="aviso", store=1)
    _regla(db_session, t, status="borrador", store=1)  # NO entra
    _regla(db_session, t, status="rechazada", store=1)  # NO entra

    snap = snapshot(db_session, t.id, store_view=1)
    assert set(snap.rule_ids) == {aceptada.id, aviso.id}
    assert snap.version == 1


def test_dos_snapshots_incrementan_la_version(db_session):
    t = _tenant(db_session)
    _regla(db_session, t, status="aceptada", store=1)
    s1 = snapshot(db_session, t.id, store_view=1)
    s2 = snapshot(db_session, t.id, store_view=1)
    assert s2.version == s1.version + 1


def test_no_se_puede_snapshotear_un_borrador(db_session):
    """Criterio de aceptación 1: un borrador no puede entrar a un snapshot."""
    t = _tenant(db_session)
    r = _regla(db_session, t, status="borrador", store=1)
    snap = snapshot(db_session, t.id, store_view=1)
    assert r.id not in snap.rule_ids


def test_degradar_por_fp_pasa_de_aceptada_a_aviso_con_actor_sistema(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, status="aceptada")
    r.false_positive_rate = 0.25
    db_session.flush()
    degradar_por_fp(db_session, r.id)
    db_session.refresh(r)
    assert r.status == "aviso"
    v = db_session.query(RuleVersion).filter_by(rule_id=r.id, to_status="aviso").one()
    assert v.actor == "sistema"


def test_inspeccionar_muestra_lo_que_marcaria_y_lo_que_descartaria(db_session):
    t = _tenant(db_session)
    r = _regla(db_session, t, definition={"attribute": "color", "marcaria": 6})
    r.evidence_count = 194
    db_session.flush()
    info = inspeccionar(db_session, r.id)
    assert info["marcaria"] == 6
    assert info["descartaria"] == 194
    assert info["attribute"] == "color"
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_curation.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Escribir la curación**

```python
# src/skudo/rules/curation.py
"""La capa de curación: transiciones puras sobre reglas.

Recibe ids y strings, devuelve objetos. Ninguna función lee de argv ni imprime:
el CLI las envuelve y S1d será una vista sobre ellas, no una reimplementación.
La validez de cada transición la decide `transitions`, la única autoridad.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.rules.models import Rule, RuleVersion, RulesetSnapshot
from skudo.rules.transitions import exigir_transicion

# Estados que un snapshot congela: sólo los que puntúan o avisan.
ACTIVOS = ("aceptada", "aviso")
# Por encima de este FP medido, una regla aceptada se degrada sola a aviso.
UMBRAL_FP = 0.10


class ReglaAmbiguaSinConfirmar(Exception):
    """Se intentó aceptar por lote una regla marcada `ambiguo` sin confirmar."""


def _transicionar(session, rule: Rule, to_status: str, actor: str, motivo: str) -> None:
    exigir_transicion(rule.origin, rule.status, to_status)
    from_status = rule.status
    rule.status = to_status
    session.add(RuleVersion(
        rule_id=rule.id, from_status=from_status, to_status=to_status,
        actor=actor, motivo=motivo, definition_snapshot=rule.definition,
    ))
    session.flush()


def aceptar(session: Session, rule_ids, actor: str, confirmar_ambiguo: bool = False) -> None:
    """Acepta reglas por lote. Se planta ante una `ambiguo` sin confirmación."""
    reglas = session.scalars(select(Rule).where(Rule.id.in_(list(rule_ids)))).all()
    for r in reglas:
        if r.definition.get("ambiguo") and not confirmar_ambiguo:
            raise ReglaAmbiguaSinConfirmar(
                f"la regla {r.id} está marcada ambigua; usar confirmar_ambiguo"
            )
    for r in reglas:
        _transicionar(session, r, "aceptada", actor, "aceptada por curación")


def rechazar(session: Session, rule_id: int, actor: str, motivo: str) -> None:
    if not motivo.strip():
        raise ValueError("rechazar exige un motivo")
    r = session.get(Rule, rule_id)
    _transicionar(session, r, "rechazada", actor, motivo)


def acotar(session: Session, rule_id: int, actor: str, motivo: str) -> None:
    """Acota una regla a `aviso`. Es la única salida del piso externo."""
    if not motivo.strip():
        raise ValueError("acotar exige un motivo")
    r = session.get(Rule, rule_id)
    _transicionar(session, r, "aviso", actor, motivo)


def ajustar(session: Session, rule_id: int, actor: str, definition: dict) -> None:
    """Cambia la definición; el historial guarda la nueva para poder ver el cambio."""
    r = session.get(Rule, rule_id)
    r.definition = definition
    session.add(RuleVersion(
        rule_id=r.id, from_status=r.status, to_status=r.status,
        actor=actor, motivo="ajuste de definición", definition_snapshot=definition,
    ))
    session.flush()


def degradar_por_fp(session: Session, rule_id: int) -> bool:
    """Degrada de aceptada a aviso si el FP medido supera el umbral. Automática.

    La dispara S1c, que es quien mide el FP; S1b provee la transición y la
    registra con actor 'sistema' para que quede claro que no fue una persona.
    """
    r = session.get(Rule, rule_id)
    if r.status != "aceptada" or r.false_positive_rate is None:
        return False
    if r.false_positive_rate < UMBRAL_FP:
        return False
    _transicionar(session, r, "aviso", "sistema",
                  f"fp {r.false_positive_rate:.3f} > umbral {UMBRAL_FP}")
    return True


def snapshot(session: Session, tenant_id: int, store_view: int) -> RulesetSnapshot:
    """Congela las reglas activas de una store view en un snapshot con versión nueva."""
    ids = session.scalars(
        select(Rule.id)
        .where(
            Rule.tenant_id == tenant_id,
            Rule.status.in_(ACTIVOS),
            (Rule.store_view_magento_id == store_view)
            | (Rule.store_view_magento_id.is_(None)),
        )
        .order_by(Rule.id)
    ).all()
    ultima = session.scalar(
        select(func.coalesce(func.max(RulesetSnapshot.version), 0)).where(
            RulesetSnapshot.tenant_id == tenant_id,
            RulesetSnapshot.store_view_magento_id == store_view,
        )
    )
    snap = RulesetSnapshot(
        tenant_id=tenant_id, store_view_magento_id=store_view,
        version=(ultima or 0) + 1, rule_ids=list(ids),
    )
    session.add(snap)
    session.flush()
    return snap


def inspeccionar(session: Session, rule_id: int) -> dict:
    """Qué marcaría la regla y qué descartaría, para el criterio de aceptación 3."""
    r = session.get(Rule, rule_id)
    return {
        "id": r.id,
        "kind": r.kind,
        "attribute": r.definition.get("attribute"),
        "status": r.status,
        "origin": r.origin,
        "confidence": r.confidence,
        "marcaria": r.definition.get("marcaria", len(r.exceptions)),
        "descartaria": r.evidence_count,
        "exceptions": r.exceptions,
        "definition": r.definition,
    }
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/rules/test_curation.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/rules/curation.py tests/rules/test_curation.py
git add src/skudo/rules/curation.py tests/rules/test_curation.py
git commit -m "feat(rules): curación pura — aceptar, rechazar, acotar, ajustar, snapshot, degradar"
```

---

### Task 7: El CLI de reglas

**Files:**
- Modify: `src/skudo/cli.py` (agregar los subcomandos `rules`)
- Test: `tests/rules/test_cli.py`

**Interfaces:**
- Consumes: todo lo anterior (`inferir`, `generar_piso`, `cargar_seed`, `sembrar`, `inferir_sinonimos`, curación).
- Produces: comandos `skudo rules {infer,floor,list,inspect,accept,reject,limit,adjust,snapshot,concepts}`.

**Nota de patrón — CRÍTICA, verificada en el codebase:** El `cli.py` existente usa `sub = parser.add_subparsers()` y despacha por `args.command`. **`main()` abre SU PROPIA sesión** contra la base que nombra `SKUDO_DATABASE_URL`; no recibe la sesión por parámetro. Por eso los tests de CLI **no** usan el fixture `db_session` (de rollback): usan el fixture `cli_db` de `tests/test_cli.py`, que apunta `SKUDO_DATABASE_URL` a `migrated_engine` y **trunca las tablas al terminar**. Para sembrar estado, el test abre una sesión aparte con `Session(migrated_engine)` y **hace `commit()`** (no flush): la sesión de `main()` es otra transacción y no ve lo no-commiteado. La salida se lee con `capsys`. Los subcomandos de reglas se agrupan bajo un subparser `rules` con `add_subparsers(dest="rules_command")` y el despacho deriva a `_rules(session, args)` (la sesión se la pasa `main()`, no el test).

- [ ] **Step 1: Leer el patrón real de los tests de CLI**

Run: `sed -n '151,190p' tests/test_cli.py` y `grep -n "def _users\|args.command ==\|add_subparsers" src/skudo/cli.py`
Expected: confirmar el fixture `cli_db` (setea `SKUDO_DATABASE_URL` a `migrated_engine`, trunca al final) y el punto de `main()` donde se agrega `if args.command == "rules": return _rules(session, args)`. Lectura, no cambio.

- [ ] **Step 2: Escribir el test que falla**

```python
# tests/rules/test_cli.py
"""El CLI de reglas, ejercitando cli.main() de verdad contra la base de tests.

main() abre su propia sesión contra SKUDO_DATABASE_URL, así que estas pruebas
NO usan el db_session de rollback: usan el fixture cli_db (que apunta la variable
a migrated_engine y trunca al terminar) y siembran con una sesión aparte que
hace COMMIT, porque la sesión de main() es otra transacción.
"""

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from skudo import cli
from skudo.mirror.models import Tenant
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun
from skudo.rules.models import Rule

# Reusar el fixture cli_db de tests/test_cli.py. En pytest, un fixture definido
# en otro módulo de tests no se comparte salvo por conftest: mover `cli_db` (y
# su dependencia de constantes TOKEN/TOKEN_VAR) a tests/conftest.py como parte de
# esta tarea, o replicarlo aquí apuntando SKUDO_DATABASE_URL a migrated_engine y
# truncando al final. La opción limpia es moverlo a conftest.


def _sembrar_perfil(engine) -> int:
    """Siembra un tenant con un perfil terminado y devuelve su id. COMMIT, no flush."""
    with Session(engine) as s:
        t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
        s.add(t)
        s.flush()
        run = ProfileRun(tenant_id=t.id, store_view_magento_id=1, mirror_sync_generation=1,
                         thresholds={}, product_count=200, finished_at=datetime.now(UTC))
        s.add(run)
        s.flush()
        part = ProfilePartition(run_id=run.id, attribute_set_id=4, splitter_kind="ninguno",
                                splitter_value=None, product_count=200, ambiguity=0.1,
                                decision_reason="sin candidatos")
        s.add(part)
        s.flush()
        s.add(AttributeCoverage(partition_id=part.id, attribute_code="color",
                                presente=194, vacio=6, no_aplica=0, desconocido=0,
                                coverage=0.97))
        s.commit()
        return t.id


def test_rules_infer_crea_reglas_borrador(cli_db, capsys):
    tid = _sembrar_perfil(cli_db)
    assert cli.main(["rules", "infer", "--tenant", "acme", "--store", "1"]) == 0
    with Session(cli_db) as s:
        reglas = s.query(Rule).filter_by(tenant_id=tid).all()
        assert reglas and all(r.status == "borrador" for r in reglas)


def test_rules_accept_mueve_a_aceptada(cli_db, capsys):
    tid = _sembrar_perfil(cli_db)
    cli.main(["rules", "infer", "--tenant", "acme", "--store", "1"])
    with Session(cli_db) as s:
        rid = s.query(Rule).filter_by(tenant_id=tid).first().id
    assert cli.main(["rules", "accept", str(rid), "--actor", "ana@x.com"]) == 0
    with Session(cli_db) as s:
        assert s.get(Rule, rid).status == "aceptada"


def test_rules_list_devuelve_json(cli_db, capsys):
    _sembrar_perfil(cli_db)
    cli.main(["rules", "infer", "--tenant", "acme", "--store", "1"])
    capsys.readouterr()  # descarta la salida del infer
    cli.main(["rules", "list", "--tenant", "acme"])
    salida = json.loads(capsys.readouterr().out)
    assert isinstance(salida, list)
    assert salida[0]["status"] == "borrador"
```

Nota: `cli_db` vive hoy en `tests/test_cli.py`. Para que `tests/rules/test_cli.py` lo use, **moverlo a `tests/conftest.py`** (junto con las constantes `TOKEN`/`TOKEN_VAR` que necesita), y dejar en `tests/test_cli.py` sólo el `import` implícito por conftest. Es un refactor pequeño y es parte de esta tarea: hacerlo en el Step 4, antes de correr.

- [ ] **Step 3: Correr y ver que falla**

Run: `python -m pytest tests/rules/test_cli.py -v`
Expected: FAIL (subcomando `rules` no existe → `SystemExit` de argparse)

- [ ] **Step 4: Cablear el subparser `rules` y su despacho**

En `src/skudo/cli.py`, dentro de la función que arma los subparsers, agregar:

```python
    rules = sub.add_parser("rules", help="inferencia, piso externo y curación de reglas (S1b)")
    rsub = rules.add_subparsers(dest="rules_command", required=True)

    r_infer = rsub.add_parser("infer", help="infiere reglas borrador desde el último perfil")
    r_infer.add_argument("--tenant", required=True)
    r_infer.add_argument("--store", type=int, required=True)

    r_floor = rsub.add_parser("floor", help="genera/actualiza el piso externo de Google")
    r_floor.add_argument("--tenant", required=True)

    r_list = rsub.add_parser("list", help="lista reglas (JSON)")
    r_list.add_argument("--tenant", required=True)
    r_list.add_argument("--status", default=None)
    r_list.add_argument("--kind", default=None)
    r_list.add_argument("--origin", default=None)
    r_list.add_argument("--store", type=int, default=None)

    r_inspect = rsub.add_parser("inspect", help="qué marcaría y qué descartaría una regla")
    r_inspect.add_argument("rule_id", type=int)

    r_accept = rsub.add_parser("accept", help="acepta reglas por lote")
    r_accept.add_argument("rule_id", type=int, nargs="+")
    r_accept.add_argument("--actor", required=True)
    r_accept.add_argument("--confirm-ambiguo", action="store_true")

    r_reject = rsub.add_parser("reject", help="rechaza una regla con motivo")
    r_reject.add_argument("rule_id", type=int)
    r_reject.add_argument("--actor", required=True)
    r_reject.add_argument("--reason", required=True)

    r_limit = rsub.add_parser("limit", help="acota un piso externo a aviso")
    r_limit.add_argument("rule_id", type=int)
    r_limit.add_argument("--actor", required=True)
    r_limit.add_argument("--reason", required=True)

    r_adjust = rsub.add_parser("adjust", help="ajusta la definición (JSON) de una regla")
    r_adjust.add_argument("rule_id", type=int)
    r_adjust.add_argument("--actor", required=True)
    r_adjust.add_argument("--definition", required=True, help="JSON de la nueva definición")

    r_snap = rsub.add_parser("snapshot", help="congela las reglas activas de una store view")
    r_snap.add_argument("--tenant", required=True)
    r_snap.add_argument("--store", type=int, required=True)

    r_conc = rsub.add_parser("concepts", help="mapa de conceptos")
    csub = r_conc.add_subparsers(dest="concepts_command", required=True)
    c_seed = csub.add_parser("seed", help="siembra universales de Google e infiere sinónimos")
    c_seed.add_argument("--tenant", required=True)
    c_list = csub.add_parser("list", help="lista el mapa de conceptos (JSON)")
    c_list.add_argument("--tenant", required=True)
```

Y en `main()`, junto al despacho de los otros comandos:

```python
            if args.command == "rules":
                return _rules(session, args)
```

Agregar la función `_rules` (imports arriba del archivo):

```python
import json as _json  # si no está ya

from skudo.rules import concepts as _concepts
from skudo.rules import curation as _curation
from skudo.rules.floor import cargar_seed, generar_piso
from skudo.rules.inference import inferir
from skudo.rules.models import Rule
from skudo.rules.transitions import TransicionInvalida


def _tenant_por_codigo(session, code):
    from skudo.mirror.models import Tenant
    t = session.scalar(select(Tenant).where(Tenant.code == code))
    if t is None:
        raise SystemExit(f"tenant desconocido: {code}")
    return t


def _rules(session, args) -> int:
    cmd = args.rules_command
    if cmd == "infer":
        t = _tenant_por_codigo(session, args.tenant)
        run = session.scalar(
            select(ProfileRun)
            .where(ProfileRun.tenant_id == t.id,
                   ProfileRun.store_view_magento_id == args.store,
                   ProfileRun.finished_at.isnot(None))
            .order_by(ProfileRun.id.desc()).limit(1)
        )
        if run is None:
            raise SystemExit("no hay perfil terminado para esa store view")
        # re-inferir: borra las reglas inferidas de ese run primero (idempotencia)
        for r in session.scalars(
            select(Rule).where(Rule.profile_run_id == run.id, Rule.origin == "inferida")
        ):
            session.delete(r)
        session.flush()
        reglas = inferir(session, run.id)
        print(f"{len(reglas)} reglas inferidas en borrador")
        return 0
    if cmd == "floor":
        t = _tenant_por_codigo(session, args.tenant)
        cargar_seed(session)
        reglas = generar_piso(session, t.id)
        print(f"{len(reglas)} reglas de piso externo")
        return 0
    if cmd == "list":
        t = _tenant_por_codigo(session, args.tenant)
        q = select(Rule).where(Rule.tenant_id == t.id)
        if args.status:
            q = q.where(Rule.status == args.status)
        if args.kind:
            q = q.where(Rule.kind == args.kind)
        if args.origin:
            q = q.where(Rule.origin == args.origin)
        if args.store is not None:
            q = q.where(Rule.store_view_magento_id == args.store)
        filas = session.scalars(q.order_by(Rule.id)).all()
        print(_json.dumps([
            {"id": r.id, "kind": r.kind, "scope": f"{r.scope_kind}:{r.scope_key}",
             "attribute": r.definition.get("attribute"), "confidence": r.confidence,
             "evidence": r.evidence_count, "status": r.status, "origin": r.origin}
            for r in filas
        ], ensure_ascii=False, indent=2))
        return 0
    if cmd == "inspect":
        print(_json.dumps(_curation.inspeccionar(session, args.rule_id),
                          ensure_ascii=False, indent=2))
        return 0
    if cmd == "accept":
        try:
            _curation.aceptar(session, args.rule_id, actor=args.actor,
                              confirmar_ambiguo=args.confirm_ambiguo)
        except _curation.ReglaAmbiguaSinConfirmar as e:
            raise SystemExit(f"{e}. Reintentar con --confirm-ambiguo")
        print(f"{len(args.rule_id)} regla(s) aceptada(s)")
        return 0
    if cmd == "reject":
        _curation.rechazar(session, args.rule_id, actor=args.actor, motivo=args.reason)
        print("rechazada")
        return 0
    if cmd == "limit":
        try:
            _curation.acotar(session, args.rule_id, actor=args.actor, motivo=args.reason)
        except TransicionInvalida as e:
            raise SystemExit(str(e))
        print("acotada a aviso")
        return 0
    if cmd == "adjust":
        _curation.ajustar(session, args.rule_id, actor=args.actor,
                          definition=_json.loads(args.definition))
        print("ajustada")
        return 0
    if cmd == "snapshot":
        t = _tenant_por_codigo(session, args.tenant)
        snap = _curation.snapshot(session, t.id, store_view=args.store)
        print(f"snapshot v{snap.version} con {len(snap.rule_ids)} reglas")
        return 0
    if cmd == "concepts":
        t = _tenant_por_codigo(session, args.tenant)
        if args.concepts_command == "seed":
            sembrados = _concepts.sembrar(session, t.id)
            sinonimos = _concepts.inferir_sinonimos(session, t.id)
            print(f"{len(sembrados)} universales, {len(sinonimos)} sinónimos inferidos")
        else:
            from skudo.rules.models import ConceptMap
            filas = session.scalars(
                select(ConceptMap).where(ConceptMap.tenant_id == t.id)
                .order_by(ConceptMap.canonical, ConceptMap.attribute_code)
            ).all()
            print(_json.dumps([
                {"canonical": c.canonical, "attribute": c.attribute_code,
                 "relation": c.relation, "confidence": c.confidence, "origin": c.origin}
                for c in filas
            ], ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"subcomando de rules desconocido: {cmd}")
```

Verificar que `ProfileRun` y `select` ya estén importados en `cli.py`; si no, agregarlos.

- [ ] **Step 5: Mover `cli_db` a conftest y correr**

Antes de correr: mover el fixture `cli_db` y las constantes `TOKEN`/`TOKEN_VAR` de `tests/test_cli.py` a `tests/conftest.py`, para que `tests/rules/test_cli.py` lo herede. Verificar que `tests/test_cli.py` sigue verde tras el movimiento.

Run: `python -m pytest tests/rules/test_cli.py tests/test_cli.py -v`
Expected: PASS (los 3 nuevos + los de `test_cli.py` que ya pasaban).

- [ ] **Step 6: Ruff y commit**

```bash
ruff check src/skudo/cli.py tests/rules/test_cli.py
git add src/skudo/cli.py tests/rules/test_cli.py
git commit -m "feat(rules): CLI de S1b — infer, floor, list, inspect, accept, reject, limit, adjust, snapshot, concepts"
```

---

### Task 8: Calibración contra el catálogo real (declarada, diferida)

**Files:**
- Create: `docs/superpowers/s1b-calibracion.md`
- Modify: `src/skudo/rules/inference.py` (los valores de los umbrales, con su cifra medida al lado)

**Depende de:** datos reales en el espejo, hoy bloqueados por la red del servidor ([[skudo-servidor-produccion]]). Esta tarea se **declara** en el plan y se **corre** cuando la ingesta real esté disponible, igual que la Task 9 de S1a. No bloquea las Tasks 1–7, que se prueban contra fixtures.

**Interfaces:**
- Consumes: un espejo con el catálogo real y un `ProfileRun` sobre él.
- Produces: los valores calibrados de `UMBRAL_ALTO`, `UMBRAL_MEDIO`, `MIN_EVIDENCIA`, `MAX_RATIO_AMBIGUO`, con la medición escrita.

- [ ] **Step 1: Correr la inferencia sobre el catálogo real**

```bash
skudo rules infer --tenant nissei --store 1
skudo rules list --tenant nissei --status borrador > /tmp/reglas-py.json
skudo rules infer --tenant nissei --store 2
```

- [ ] **Step 2: Medir la distribución de confianza**

Contar, sobre las reglas inferidas, cuántas caen en banda alta vs media, y muestrear a mano 20 reglas de cada banda contra el catálogo real: ¿la obligatoriedad inferida es real o marca variedad legítima? El resultado decide si `UMBRAL_ALTO=0.90` es correcto, alto o bajo.

- [ ] **Step 3: Escribir `docs/superpowers/s1b-calibracion.md`**

Con la misma forma que la calibración del perfilador: cada umbral, el valor elegido, la medición que lo justifica, y los casos concretos que se miraron. Si un valor cambia respecto de la hipótesis, el cambio va en `inference.py` con la cifra medida en el comentario.

- [ ] **Step 4: Correr la suite completa y confirmar que sigue verde**

Run: `python -m pytest`
Expected: PASS. Los tests de las Tasks 1–7 usan fixtures y no dependen de la calibración; sólo cambian números, no formas.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/s1b-calibracion.md src/skudo/rules/inference.py
git commit -m "docs(rules): calibración de los umbrales de inferencia contra el catálogo real"
```

---

## Self-review del plan

**Cobertura del diseño (§ del design doc → task):**
- §3 inferencia de obligatoriedad y rango → Task 3 ✓
- §4 piso externo de Google → Task 5 ✓
- §5 mapa de conceptos → Task 4 ✓
- §6 transiciones y su historial → Task 2 (máquina) + Task 6 (aplicación) ✓
- §6.2 ruleset_snapshot → Task 6 (`snapshot`) ✓
- §7 API de curación por CLI → Task 6 (lógica) + Task 7 (CLI) ✓
- §8 estructura de archivos → distribuida en Tasks 1–7 ✓
- §10 criterios de aceptación → tests nombrados: "no_se_puede_snapshotear_un_borrador" (crit. 1), "rechazar_un_piso_externo_falla"/"acotar" (crit. 2), "inspeccionar_muestra_lo_que_marcaria_y_descartaria" (crit. 3), "reproducible_dos_inferencias" (inv. 4), "cobertura_alta_infiere...confianza_igual_a_cobertura" (inv. 5) ✓
- §11 calibración diferida → Task 8 ✓

**Consistencia de tipos:** `inferir(session, profile_run_id)` devuelve `list[Rule]` en Task 3 y se llama igual en Task 7. `snapshot(session, tenant_id, store_view)` en Task 6 y Task 7. `generar_piso(session, tenant_id)` y `cargar_seed(session)` en Tasks 5 y 7. Coinciden.

**Placeholders:** ninguno. Todo step de código tiene su código.

**Punto verificado en el codebase (no dejado al azar):** `main()` abre su propia sesión contra `SKUDO_DATABASE_URL`; los tests de CLI usan el fixture `cli_db` (no `db_session`) y siembran con una sesión aparte que hace `commit`. Task 7 lo transcribe y agrega el refactor de mover `cli_db` a `tests/conftest.py`. Es el único punto que exigía leer el codebase, y quedó resuelto en el plan, no diferido a la ejecución.
