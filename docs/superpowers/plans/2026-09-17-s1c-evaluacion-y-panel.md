# S1c-1 — Evaluación por reglas y su panel: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir las reglas aceptadas de S1b + el espejo en hallazgos atribuidos y puntuados, unificados con los detectores existentes, y mostrarlo en el panel (vista de reglas y hallazgos por origen).

**Architecture:** Un motor puro (`findings/rules_eval.py`) evalúa un `RulesetSnapshot` contra el espejo con tres evaluadores (obligatoriedad/rango/formato) y produce el mismo `Resultado(hallazgos, cobertura)` que los detectores de `catalog.py`. `detect_store_view` corre detectores + reglas y sella la versión de ruleset. `scoring.py` deduplica por causa raíz. El panel gana un endpoint `/rules` y vistas de lectura. Datos demo hasta que el servidor alcance el Magento.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, Postgres, FastAPI, SPA vanilla JS (skill impeccable para lo visual), pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-s1c-evaluacion-y-panel-design.md` (que depende de `2026-09-11-s1-perfilador-design.md` §7 y del spec maestro §6.4).

## Global Constraints

- **Sólo lo `aceptada` puntúa como defecto; `aviso` pesa 0; `borrador`/`rechazada` no entran al snapshot.** El motor evalúa contra un `RulesetSnapshot` congelado.
- **Cuatro estados vía `profile/states.py::attribute_state`** — la MISMA función que el perfilador y S1b. `desconocido` va a `no_evaluado`, nunca a carencia ni a cobertura. Prohibido reimplementar el predicado.
- **Las reglas son ADITIVAS**: los detectores de S0 (`catalog.py`) no se tocan ni se borran.
- **`rango` y `formato` producen severidad `candidato`** (peso 3); `obligatoriedad` la severidad que la regla declare (default `media`).
- **Atribución**: un hallazgo de regla lleva `rule_id` y su pasada lleva `ruleset_version`; un hallazgo de detector especial los deja NULL.
- **Sin doble penalización**: un atributo marcado por detector y por regla en el mismo producto pesa una vez (dedup por causa raíz en `scoring.py`).
- **El panel de S1c-1 es de LECTURA** (no cura reglas; eso es S1d). Endpoints nuevos son GET.
- **Reproducibilidad**: dos evaluaciones contra el mismo snapshot y la misma generación del espejo dan los mismos hallazgos.
- Código de hallazgo de regla: `f"regla:{kind}:{attribute}"` (p.ej. `regla:obligatoriedad:color`).
- Nombres de tabla en singular. Migración nueva = `0020`, `down_revision="0019"`.

---

### Task 1: Atribución en el esquema

**Files:**
- Modify: `src/skudo/findings/models.py`
- Create: `alembic/versions/0020_finding_rule_attribution.py`
- Modify (si el test de invariante lo exige): `tests/mirror/test_mirror_has_no_orphans.py`
- Test: `tests/findings/test_models_attribution.py`

**Interfaces:**
- Consumes: `Finding`, `FindingRun` (existentes); `Rule` (`skudo.rules.models`, FK target).
- Produces: `Finding.rule_id` (int|None), `Finding.ruleset_version` (int|None), `FindingRun.ruleset_version` (int|None).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/findings/test_models_attribution.py
from skudo.mirror.models import Tenant
from skudo.findings.models import Finding, FindingRun


def _tenant(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t); session.flush()
    return t


def test_una_pasada_sella_su_version_de_ruleset(db_session):
    t = _tenant(db_session)
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=0, ruleset_version=7)
    db_session.add(run); db_session.flush()
    assert run.ruleset_version == 7


def test_un_hallazgo_de_regla_lleva_rule_id_y_version(db_session):
    t = _tenant(db_session)
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=0)
    db_session.add(run); db_session.flush()
    f = Finding(run_id=run.id, code="regla:obligatoriedad:color", axis=3,
                severity="media", subject_type="producto", subject_key="P1",
                evidence={"attribute": "color", "rule_id": 42},
                rule_id=None, ruleset_version=7)
    db_session.add(f); db_session.flush()
    assert f.ruleset_version == 7


def test_un_hallazgo_de_detector_deja_la_atribucion_nula(db_session):
    t = _tenant(db_session)
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1,
                     mirror_sync_generation=1, product_count=0)
    db_session.add(run); db_session.flush()
    f = Finding(run_id=run.id, code="sin_imagen", axis=7, severity="alta",
                subject_type="producto", subject_key="P1", evidence={})
    db_session.add(f); db_session.flush()
    assert f.rule_id is None and f.ruleset_version is None
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/findings/test_models_attribution.py -v`
Expected: FAIL (`TypeError: 'rule_id' is an invalid keyword argument` o similar)

- [ ] **Step 3: Agregar las columnas al modelo**

En `src/skudo/findings/models.py`, agregar a `FindingRun` (después de `product_count`):

```python
    # La versión del ruleset con la que se evaluaron las reglas de esta pasada.
    # NULL si no había snapshot (sólo corrieron los detectores especiales).
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

Y a `Finding` (después de `evidence`):

```python
    # De qué regla salió el hallazgo. NULL para los detectores especiales, que
    # no nacen de una regla. Es lo que deja al panel decir "esto lo produjo esta
    # regla".
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("rule.id"), nullable=True, index=True
    )
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

Verificar que `ForeignKey` ya está importado en el archivo (lo está).

- [ ] **Step 4: Escribir la migración**

```python
# alembic/versions/0020_finding_rule_attribution.py
"""finding rule attribution

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("finding_run", sa.Column("ruleset_version", sa.Integer(), nullable=True))
    op.add_column("finding", sa.Column("rule_id", sa.Integer(),
                                       sa.ForeignKey("rule.id"), nullable=True))
    op.add_column("finding", sa.Column("ruleset_version", sa.Integer(), nullable=True))
    op.create_index("ix_finding_rule_id", "finding", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_rule_id", "finding")
    op.drop_column("finding", "ruleset_version")
    op.drop_column("finding", "rule_id")
    op.drop_column("finding_run", "ruleset_version")
```

- [ ] **Step 5: Correr y ver que pasa; ajustar la invariante del espejo si aplica**

Run: `python -m pytest tests/findings/test_models_attribution.py -v`
Expected: PASS (3 tests).

Luego correr la suite completa: `python -m pytest -q`. Si `tests/mirror/test_mirror_has_no_orphans.py` falla porque `finding` ahora tiene una FK nueva a `rule`, agregar la arista `finding → rule` a su declaración de aristas (patrón: la FK es nullable, así que es una arista opcional; seguir cómo el test clasifica las demás FK opcionales — mirar cómo trata las columnas nullable existentes). Si no falla, no tocar nada.

- [ ] **Step 6: Ruff y commit**

```bash
ruff check src/skudo/findings/models.py alembic/versions/0020_finding_rule_attribution.py tests/findings/test_models_attribution.py
git add src/skudo/findings/models.py alembic/versions/0020_finding_rule_attribution.py tests/findings/test_models_attribution.py
git commit -m "feat(findings): atribución de hallazgos a su regla y versión de ruleset (0020)"
```

---

### Task 2: Los tres evaluadores de reglas

**Files:**
- Create: `src/skudo/findings/rules_eval.py`
- Test: `tests/findings/test_rules_eval.py`

**Interfaces:**
- Consumes: `Ficha`, `Hallazgo`, `Cobertura`, `Resultado`, `_particionar`, `_publicado`, severidades (`MEDIA`, `CANDIDATO`) de `skudo.findings.catalog`; `attribute_state`, `State` de `skudo.profile.states`; `Rule` de `skudo.rules.models`.
- Produces: `ReglaEvaluable` (dataclass: id, kind, axis, severity, scope_kind, scope_key, store_view, definition), `evaluar_regla(regla, fichas, sets_by_code) -> Resultado`, `codigo_de(kind, attribute) -> str`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/findings/test_rules_eval.py
from skudo.findings.catalog import Ficha, CANDIDATO, MEDIA
from skudo.findings.rules_eval import ReglaEvaluable, codigo_de, evaluar_regla

SETS = {"color": frozenset({4}), "peso": frozenset({4})}


def ficha(sku, **attrs):
    base = {"status": "1", "visibility": "4"}  # publicado
    base.update(attrs)
    return Ficha(sku=sku, attributes=base, attribute_set_id=4, type_id="simple")


def _regla(kind, attribute, **defn):
    return ReglaEvaluable(
        id=1, kind=kind, axis=3, severity=MEDIA if kind == "obligatoriedad" else CANDIDATO,
        scope_kind="attribute_set", scope_key="4", store_view=1,
        definition={"attribute": attribute, **defn},
    )


def test_obligatoriedad_marca_el_vacio_no_el_presente():
    regla = _regla("obligatoriedad", "color")
    fichas = [ficha("A", color="rojo"), ficha("B")]  # B no tiene color
    res = evaluar_regla(regla, fichas, SETS)
    skus = {h.subject_key for h in res.hallazgos}
    assert skus == {"B"}
    h = res.hallazgos[0]
    assert h.code == "regla:obligatoriedad:color"
    assert h.severity == MEDIA
    assert h.evidence["rule_id"] == 1
    assert res.cobertura.evaluados == 2  # A y B son evaluables (presente+vacio)


def test_desconocido_no_es_carencia_y_va_a_no_evaluado():
    regla = _regla("obligatoriedad", "color")
    # un producto sin attribute_set: su color es desconocido
    fichas = [Ficha(sku="X", attributes={"status": "1", "visibility": "4"},
                    attribute_set_id=None, type_id="simple")]
    res = evaluar_regla(regla, fichas, SETS)
    assert res.hallazgos == []
    assert res.cobertura.no_evaluado == 1
    assert res.cobertura.evaluados == 0


def test_rango_marca_fuera_de_limites_como_candidato():
    regla = _regla("rango", "peso", min=0.5, max=8.0)
    fichas = [ficha("A", peso="2.0"), ficha("B", peso="50")]  # B fuera
    res = evaluar_regla(regla, fichas, SETS)
    skus = {h.subject_key for h in res.hallazgos}
    assert skus == {"B"}
    assert res.hallazgos[0].severity == CANDIDATO
    assert res.hallazgos[0].evidence["valor"] == 50.0


def test_rango_ignora_el_no_numerico():
    regla = _regla("rango", "peso", min=0.5, max=8.0)
    fichas = [ficha("A", peso="liviano")]  # no numérico → no_aplica, no hallazgo
    res = evaluar_regla(regla, fichas, SETS)
    assert res.hallazgos == []
    assert res.cobertura.no_aplica == 1


def test_formato_sospecha_de_conversion_es_candidato():
    regla = _regla("formato", "peso", sospecha_conversion=True)
    fichas = [ficha("A", peso="2,5"), ficha("B", peso="3.0")]  # 2,5 ambiguo
    res = evaluar_regla(regla, fichas, SETS)
    assert any(h.subject_key == "A" for h in res.hallazgos)
    assert all(h.severity == CANDIDATO for h in res.hallazgos)


def test_fuera_de_scope_no_se_evalua():
    regla = _regla("obligatoriedad", "color")
    regla = ReglaEvaluable(**{**regla.__dict__, "scope_key": "9"})  # otro set
    fichas = [ficha("A")]  # set 4, la regla es del set 9
    res = evaluar_regla(regla, fichas, SETS)
    assert res.hallazgos == []
    assert res.cobertura.no_aplica == 1


def test_codigo_de_agrupa_por_atributo():
    assert codigo_de("obligatoriedad", "color") == "regla:obligatoriedad:color"
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/findings/test_rules_eval.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Escribir los evaluadores**

```python
# src/skudo/findings/rules_eval.py
"""El motor de evaluación de reglas: una regla del snapshot + fichas → Resultado.

Produce el MISMO par (hallazgos, cobertura) que los detectores de catalog.py, así
que enchufa en el flujo de run.py sin cambiarlo. Los cuatro estados salen de
profile/states.py —la misma función que el perfilador y S1b— para que
`desconocido` no se confunda nunca con carencia.
"""

from dataclasses import dataclass

from skudo.findings.catalog import (
    CANDIDATO,
    Cobertura,
    Ficha,
    Hallazgo,
    Resultado,
    _publicado,
)
from skudo.profile.states import State, attribute_state


@dataclass(frozen=True)
class ReglaEvaluable:
    """Lo que el motor necesita de una regla, ya resuelto desde la fila."""

    id: int
    kind: str
    axis: int
    severity: str
    scope_kind: str
    scope_key: str
    store_view: int | None
    definition: dict


def codigo_de(kind: str, attribute: str) -> str:
    """El código de un hallazgo de regla: prefijo `regla:` y agrupado por atributo."""
    return f"regla:{kind}:{attribute}"


def _en_scope(regla: ReglaEvaluable, f: Ficha) -> bool:
    """Si la ficha cae en el scope de la regla. `category` se resuelve en run.py
    (necesita las asignaciones), así que aquí una regla de categoría se considera
    aplicable y run.py la acota antes de llamar."""
    if regla.scope_kind == "global":
        return True
    if regla.scope_kind in ("attribute_set", "subtype"):
        return f.attribute_set_id is not None and str(f.attribute_set_id) == regla.scope_key
    if regla.scope_kind == "category":
        return True
    return False


def _num(valor: str | None) -> float | None:
    if valor is None:
        return None
    try:
        return float(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _es_ambiguo(valor: str | None) -> bool:
    """Un valor que NO se lee como número sin adivinar: coma decimal, texto, etc."""
    if valor is None:
        return False
    s = str(valor).strip()
    if not s:
        return False
    return _num(s) is None or ("," in s)


def evaluar_regla(
    regla: ReglaEvaluable,
    fichas,
    sets_by_code: dict[str, frozenset[int]],
) -> Resultado:
    """Evalúa UNA regla sobre las fichas. Devuelve hallazgos y cobertura."""
    attribute = regla.definition["attribute"]
    code = codigo_de(regla.kind, attribute)
    en_scope = [f for f in fichas if _en_scope(regla, f)]
    fuera = len(fichas) - len(en_scope)

    hallazgos: list[Hallazgo] = []
    evaluados = no_aplica = no_evaluado = 0
    no_aplica += fuera  # los de otro scope no aplican a esta regla

    for f in en_scope:
        if not _publicado(f):
            no_aplica += 1
            continue
        if regla.kind == "obligatoriedad":
            estado = attribute_state(f.attributes, f.attribute_set_id, attribute, sets_by_code)
            if estado is State.DESCONOCIDO:
                no_evaluado += 1
            elif estado is State.NO_APLICA:
                no_aplica += 1
            else:
                evaluados += 1
                if estado is State.VACIO:
                    hallazgos.append(Hallazgo(code, regla.axis, regla.severity,
                                              "producto", f.sku,
                                              {"attribute": attribute, "rule_id": regla.id}))
        elif regla.kind == "rango":
            valor = _num(f.valor(attribute))
            if valor is None:
                no_aplica += 1
                continue
            evaluados += 1
            lo, hi = regla.definition.get("min"), regla.definition.get("max")
            if (lo is not None and valor < lo) or (hi is not None and valor > hi):
                hallazgos.append(Hallazgo(code, regla.axis, CANDIDATO, "producto", f.sku,
                                          {"attribute": attribute, "rule_id": regla.id,
                                           "valor": valor, "min": lo, "max": hi}))
        elif regla.kind == "formato":
            crudo = f.valor(attribute)
            if crudo is None:
                no_aplica += 1
                continue
            evaluados += 1
            if regla.definition.get("sospecha_conversion") and _es_ambiguo(crudo):
                hallazgos.append(Hallazgo(code, regla.axis, CANDIDATO, "producto", f.sku,
                                          {"attribute": attribute, "rule_id": regla.id,
                                           "valor": str(crudo)}))
        else:
            no_aplica += 1  # kind no evaluable en S1c-1 (plantilla_nombre, unidad, filtrable)

    return Resultado(
        hallazgos,
        Cobertura(code, evaluados, no_aplica, no_evaluado,
                  "productos fuera del scope de la regla, no publicados, o no numéricos"),
    )
```

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/findings/test_rules_eval.py -v`
Expected: PASS (7 tests). Nota: `Ficha.valor(code)` ya existe en `catalog.py` (devuelve el valor o None si vacío). Verificarlo antes de usar; si su firma difiere, adaptarse a ella.

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/findings/rules_eval.py tests/findings/test_rules_eval.py
git add src/skudo/findings/rules_eval.py tests/findings/test_rules_eval.py
git commit -m "feat(findings): los tres evaluadores de reglas (obligatoriedad, rango, formato)"
```

---

### Task 3: Integrar el motor en la pasada de detección

**Files:**
- Modify: `src/skudo/findings/run.py`
- Test: `tests/findings/test_run_con_reglas.py`

**Interfaces:**
- Consumes: `evaluar_regla`, `ReglaEvaluable` (Task 2); `RulesetSnapshot`, `Rule` (S1b); `sets_by_code` (profile.states); `Finding`, `FindingRun`, `DetectorCoverage` (Task 1); `fichas_de`, `evaluar` (run.py/catalog.py existentes).
- Produces: `_reglas_del_snapshot(session, tenant_id, store_view, ruleset_version) -> tuple[list[ReglaEvaluable], int|None]`; `detect_store_view(session, tenant_id, store_view, ruleset_version=None)` (firma extendida, retrocompatible).

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/findings/test_run_con_reglas.py
from datetime import UTC, datetime

from skudo.mirror.models import Attribute, ProductRecord, Tenant
from skudo.findings.models import Finding, FindingRun
from skudo.findings.run import detect_store_view
from skudo.rules.models import Rule, RulesetSnapshot


def _setup(session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    session.add(t); session.flush()
    session.add(Attribute(tenant_id=t.id, code="color", label="color",
                          frontend_input="select", declared_scope="global",
                          is_filterable=True, is_required=False, attribute_set_ids=[4]))
    # dos productos publicados del set 4; uno sin color
    for sku, attrs in [("A", {"status": "1", "visibility": "4", "color": "rojo"}),
                       ("B", {"status": "1", "visibility": "4"})]:
        session.add(ProductRecord(tenant_id=t.id, sku=sku, store_view_magento_id=1,
                                  attributes=attrs, attribute_set_id=4, type_id="simple",
                                  sync_generation=1, scope_provenance={}, content_hash=sku))
    session.flush()
    return t


def _regla_aceptada(session, t, **kw):
    r = Rule(tenant_id=t.id, scope_kind="attribute_set", scope_key="4",
             store_view_magento_id=1, axis=3, kind="obligatoriedad",
             definition={"attribute": "color", "marcaria": 1}, confidence=0.97,
             evidence_count=1, exceptions=[], status="aceptada", origin="inferida")
    for k, v in kw.items():
        setattr(r, k, v)
    session.add(r); session.flush()
    return r


def test_una_regla_aceptada_en_el_snapshot_produce_hallazgo_atribuido(db_session):
    t = _setup(db_session)
    r = _regla_aceptada(db_session, t)
    snap = RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                           rule_ids=[r.id])
    db_session.add(snap); db_session.flush()

    run = detect_store_view(db_session, t.id, 1)
    reglas = db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "regla:obligatoriedad:color").all()
    assert len(reglas) == 1
    assert reglas[0].subject_key == "B"
    assert reglas[0].rule_id == r.id
    assert reglas[0].ruleset_version == 1
    assert run.ruleset_version == 1


def test_una_regla_borrador_no_esta_en_el_snapshot_y_no_marca(db_session):
    t = _setup(db_session)
    _regla_aceptada(db_session, t, status="borrador")
    # sin snapshot creado: el motor de reglas no aporta, detectores especiales sí
    run = detect_store_view(db_session, t.id, 1)
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code.like("regla:%")).count() == 0
    assert run.ruleset_version is None


def test_los_detectores_especiales_siguen_corriendo(db_session):
    t = _setup(db_session)
    run = detect_store_view(db_session, t.id, 1)
    # sin_categoria es un detector especial: B (y A) no tienen categoría
    assert db_session.query(Finding).filter(
        Finding.run_id == run.id, Finding.code == "sin_categoria").count() >= 1
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/findings/test_run_con_reglas.py -v`
Expected: FAIL (los hallazgos de regla no se producen; `run.ruleset_version` no existe aún como comportamiento).

- [ ] **Step 3: Extender `detect_store_view`**

En `src/skudo/findings/run.py`, agregar imports:

```python
from sqlalchemy import func
from skudo.findings.rules_eval import ReglaEvaluable, evaluar_regla
from skudo.rules.models import Rule, RulesetSnapshot
from skudo.profile.states import sets_by_code
from skudo.mirror.models import ProductCategoryAssignment
```

Agregar la resolución del snapshot:

```python
def _reglas_del_snapshot(
    session, tenant_id, store_view_magento_id, ruleset_version=None
) -> tuple[list[ReglaEvaluable], int | None]:
    """Las reglas activas del snapshot pedido (o el último) como ReglaEvaluable.

    Sin snapshot, no hay reglas: el motor no aporta y la pasada lo declara con
    ruleset_version = None. Los detectores especiales corren igual.
    """
    q = select(RulesetSnapshot).where(
        RulesetSnapshot.tenant_id == tenant_id,
        RulesetSnapshot.store_view_magento_id == store_view_magento_id,
    )
    if ruleset_version is not None:
        q = q.where(RulesetSnapshot.version == ruleset_version)
    snap = session.scalars(q.order_by(RulesetSnapshot.version.desc()).limit(1)).first()
    if snap is None:
        return [], None
    filas = session.scalars(
        select(Rule).where(Rule.id.in_(snap.rule_ids)).order_by(Rule.id)
    ).all()
    reglas = [
        ReglaEvaluable(
            id=r.id, kind=r.kind, axis=r.axis,
            # una regla en `aviso` no penaliza: su severidad efectiva es 'aviso'
            severity=("aviso" if r.status == "aviso" else _severidad_base(r)),
            scope_kind=r.scope_kind, scope_key=r.scope_key,
            store_view=r.store_view_magento_id, definition=r.definition,
        )
        for r in filas
        if r.kind in ("obligatoriedad", "rango", "formato")
        and "attribute" in (r.definition or {})
    ]
    return reglas, snap.version


def _severidad_base(r) -> str:
    """La severidad con que puntúa una regla ACEPTADA, según su kind."""
    from skudo.findings.catalog import CANDIDATO, MEDIA
    if r.kind == "obligatoriedad":
        return r.definition.get("severidad", MEDIA)
    return CANDIDATO  # rango, formato
```

Modificar `detect_store_view` para aceptar `ruleset_version=None`, sellar la versión y correr las reglas. Dentro, después de `fichas = fichas_de(...)` y de crear `run`, además del bucle de `evaluar(fichas)`:

```python
def detect_store_view(session, tenant_id, store_view_magento_id, ruleset_version=None):
    generacion = session.scalar(...)  # (sin cambio)
    fichas = fichas_de(session, tenant_id, store_view_magento_id)
    reglas, version = _reglas_del_snapshot(
        session, tenant_id, store_view_magento_id, ruleset_version)
    run = FindingRun(
        tenant_id=tenant_id, store_view_magento_id=store_view_magento_id,
        mirror_sync_generation=generacion or 0, product_count=len(fichas),
        ruleset_version=version,
    )
    session.add(run); session.flush()

    # 1) detectores especiales (sin cambio)
    for resultado in evaluar(fichas):
        _escribir_resultado(session, run, resultado, rule_id=None, version=None)

    # 2) motor de reglas
    por_categoria = _skus_por_categoria(session, tenant_id) if any(
        r.scope_kind == "category" for r in reglas) else {}
    por_id = {r.id: r for r in reglas}
    for regla in reglas:
        fichas_regla = fichas
        if regla.scope_kind == "category":
            skus = por_categoria.get(int(regla.scope_key), set())
            fichas_regla = [f for f in fichas if f.sku in skus]
        resultado = evaluar_regla(regla, fichas_regla, _sets_by_code_cache(session, tenant_id))
        _escribir_resultado(session, run, resultado, rule_id=regla.id, version=version)

    run.finished_at = datetime.now(UTC)
    session.flush()
    return run
```

Añadir los helpers `_escribir_resultado` (que escribe `DetectorCoverage` y los `Finding` con la atribución que reciba), `_skus_por_categoria`, y un cache de `sets_by_code` por pasada. `_escribir_resultado` factoriza la escritura que hoy está inline en el bucle de detectores — mover ese código a la función y llamarlo desde ambos lugares, pasando `rule_id`/`version` (None para detectores). El `Finding` gana `rule_id=rule_id, ruleset_version=version`.

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/findings/test_run_con_reglas.py tests/findings/ -v`
Expected: PASS. Correr la suite: `python -m pytest -q` — nada existente se rompe (los llamadores de `detect_store_view` sin `ruleset_version` siguen andando; `cycle.py` no cambia).

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/findings/run.py tests/findings/test_run_con_reglas.py
git add src/skudo/findings/run.py tests/findings/test_run_con_reglas.py
git commit -m "feat(findings): la pasada evalúa el ruleset y atribuye los hallazgos de regla"
```

---

### Task 4: Dedup por causa raíz en el scoring

**Files:**
- Modify: `src/skudo/score/scoring.py`
- Modify: `src/skudo/score/run.py` (pasar la evidencia del hallazgo a la nota)
- Test: `tests/score/test_dedup_causa_raiz.py`

**Interfaces:**
- Consumes: `HallazgoDeProducto`, `nota_de_producto` (existentes).
- Produces: `HallazgoDeProducto` gana un campo opcional `causa: str | None`; `nota_de_producto` deduplica por `causa or code`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/score/test_dedup_causa_raiz.py
from skudo.score.scoring import HallazgoDeProducto, nota_de_producto
from skudo.findings.catalog import MEDIA


def test_detector_y_regla_sobre_el_mismo_atributo_descuentan_una_vez():
    # sin_descripcion (detector) y regla:obligatoriedad:description (regla) son
    # la MISMA causa: el atributo description vacío. Debe pesar una vez.
    hallazgos = [
        HallazgoDeProducto(code="sin_descripcion", severity=MEDIA, axis=6,
                           causa="description"),
        HallazgoDeProducto(code="regla:obligatoriedad:description", severity=MEDIA,
                           axis=6, causa="description"),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 1
    assert nota.puntaje == 90  # 100 - 10, una sola vez


def test_dos_atributos_distintos_descuentan_dos_veces():
    hallazgos = [
        HallazgoDeProducto(code="regla:obligatoriedad:color", severity=MEDIA, axis=3,
                           causa="color"),
        HallazgoDeProducto(code="regla:obligatoriedad:marca", severity=MEDIA, axis=3,
                           causa="marca"),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 2


def test_sin_causa_deduplica_por_code_como_antes():
    hallazgos = [
        HallazgoDeProducto(code="sin_imagen", severity="alta", axis=7),
        HallazgoDeProducto(code="sin_imagen", severity="alta", axis=7),
    ]
    nota = nota_de_producto("P1", hallazgos)
    assert len(nota.deducciones) == 1
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/score/test_dedup_causa_raiz.py -v`
Expected: FAIL (`causa` no es campo de `HallazgoDeProducto`).

- [ ] **Step 3: Agregar la causa y deduplicar por ella**

En `src/skudo/score/scoring.py`, agregar el campo a `HallazgoDeProducto`:

```python
@dataclass(frozen=True)
class HallazgoDeProducto:
    code: str
    severity: str
    axis: int
    # La causa raíz. Cuando dos hallazgos la comparten —un detector y una regla
    # sobre el mismo atributo— son UNA causa y pesan una vez (spec §6.4: sin
    # doble penalización). None → la causa es el propio code.
    causa: str | None = None
```

En `nota_de_producto`, cambiar la clave de dedup de `h.code` a `h.causa or h.code`:

```python
    vistos: dict[str, HallazgoDeProducto] = {}
    for h in hallazgos:
        clave = h.causa or h.code
        # se conserva el de mayor peso de la causa (el detector especial suele
        # ser más específico); ante empate, el primero
        actual = vistos.get(clave)
        if actual is None or PESO_POR_SEVERIDAD.get(h.severity, 0) > PESO_POR_SEVERIDAD.get(actual.severity, 0):
            vistos[clave] = h
```

El resto de la función (recorrer `vistos`, sumar pesos) queda igual, pero al construir la deducción usar `h.code` para mostrar y `clave` no importa para el peso. Verificar que la iteración final sigue siendo estable (ordenar por `clave`).

- [ ] **Step 4: Poblar la causa desde la evidencia en `score/run.py`**

En `_hallazgos_por_sku`, al construir cada `HallazgoDeProducto`, derivar la causa de la evidencia: para carencias de campo, el atributo; si no, None.

```python
        causa = (f.evidence or {}).get("attribute") or (f.evidence or {}).get("campo")
        h = HallazgoDeProducto(code=f.code, severity=f.severity, axis=f.axis, causa=causa)
```

(Los detectores especiales de carencia guardan `evidence.campo`; las reglas guardan `evidence.attribute`. Ambos alimentan la misma causa, que es lo que funde las dos marcas del mismo atributo.)

- [ ] **Step 5: Correr y ver que pasa**

Run: `python -m pytest tests/score/test_dedup_causa_raiz.py tests/score/ -v`
Expected: PASS. Suite completa `python -m pytest -q` verde — el default `causa=None` mantiene el comportamiento previo de todos los tests de score existentes.

- [ ] **Step 6: Ruff y commit**

```bash
ruff check src/skudo/score/scoring.py src/skudo/score/run.py tests/score/test_dedup_causa_raiz.py
git add src/skudo/score/scoring.py src/skudo/score/run.py tests/score/test_dedup_causa_raiz.py
git commit -m "feat(score): deduplicación por causa raíz (atributo), sin doble penalización"
```

---

### Task 5: El comando `skudo evaluate`

**Files:**
- Modify: `src/skudo/cli.py`
- Test: `tests/findings/test_cli_evaluate.py`

**Interfaces:**
- Consumes: `detect_store_view` (Task 3), `score_run` (existente), el fixture `cli_db` (ya en `tests/conftest.py` desde S1b).
- Produces: comando `evaluate --tenant X --store S [--ruleset V]`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/findings/test_cli_evaluate.py
from datetime import UTC, datetime
from sqlalchemy.orm import Session

from skudo import cli
from skudo.mirror.models import Attribute, ProductRecord, Tenant
from skudo.findings.models import Finding, FindingRun
from skudo.rules.models import Rule, RulesetSnapshot


def _seed(engine) -> int:
    with Session(engine) as s:
        t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
        s.add(t); s.flush()
        s.add(Attribute(tenant_id=t.id, code="color", label="color",
                        frontend_input="select", declared_scope="global",
                        is_filterable=True, is_required=False, attribute_set_ids=[4]))
        s.add(ProductRecord(tenant_id=t.id, sku="B", store_view_magento_id=1,
                            attributes={"status": "1", "visibility": "4"},
                            attribute_set_id=4, type_id="simple", sync_generation=1,
                            scope_provenance={}, content_hash="B"))
        r = Rule(tenant_id=t.id, scope_kind="attribute_set", scope_key="4",
                 store_view_magento_id=1, axis=3, kind="obligatoriedad",
                 definition={"attribute": "color"}, confidence=0.9, evidence_count=1,
                 exceptions=[], status="aceptada", origin="inferida")
        s.add(r); s.flush()
        s.add(RulesetSnapshot(tenant_id=t.id, store_view_magento_id=1, version=1,
                              rule_ids=[r.id]))
        s.commit()
        return t.id


def test_evaluate_corre_reglas_y_puntua(cli_db, capsys):
    tid = _seed(cli_db)
    assert cli.main(["evaluate", "--tenant", "acme", "--store", "1"]) == 0
    with Session(cli_db) as s:
        run = s.query(FindingRun).filter_by(tenant_id=tid).order_by(FindingRun.id.desc()).first()
        assert run.ruleset_version == 1
        assert s.query(Finding).filter_by(run_id=run.id,
                                          code="regla:obligatoriedad:color").count() == 1
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/findings/test_cli_evaluate.py -v`
Expected: FAIL (comando `evaluate` no existe → SystemExit de argparse).

- [ ] **Step 3: Cablear el comando**

En `build_parser()` de `cli.py`, junto a los otros subparsers de tenant, agregar (usar el patrón `tenant_command` si el archivo lo tiene, o un `sub.add_parser` con `--tenant`):

```python
    ev = sub.add_parser("evaluate",
                        help="corre detectores + motor de reglas y puntúa (S1c)")
    ev.add_argument("--tenant", required=True)
    ev.add_argument("--store", type=int, required=True)
    ev.add_argument("--ruleset", type=int, default=None,
                    help="versión del ruleset_snapshot; por defecto el último")
```

En `main()`, tras resolver el tenant (o en el bloque de comandos con `--tenant`):

```python
            if args.command == "evaluate":
                from skudo.findings.run import detect_store_view
                from skudo.score.run import score_run
                run = detect_store_view(session, tenant.id, args.store, args.ruleset)
                salud = score_run(session, run)
                session.commit()
                print(f"pasada {run.id}: ruleset v{run.ruleset_version}, "
                      f"salud {salud.salud} ({salud.grado})")
                return EXIT_OK
```

Respetar el patrón de sesión/commit del archivo (main() abre la sesión; los comandos que escriben commitean, como se estableció en S1b Task 7).

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/findings/test_cli_evaluate.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/cli.py tests/findings/test_cli_evaluate.py
git add src/skudo/cli.py tests/findings/test_cli_evaluate.py
git commit -m "feat(cli): comando evaluate — detectores + reglas + puntuación"
```

---

### Task 6: El informe por regla y el endpoint `/rules`

**Files:**
- Modify: `src/skudo/findings/run.py` (`findings_report`)
- Modify: `src/skudo/web/app.py`
- Test: `tests/web/test_rules_endpoint.py`

**Interfaces:**
- Consumes: `Finding`, `Rule`, `RulesetSnapshot`, `FindingRun`, `require_user`, `get_db` (existentes).
- Produces: `findings_report` incluye por hallazgo su `rule_id`/atribución; `GET /api/tenants/{code}/rules?store=S`.

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/web/test_rules_endpoint.py
from datetime import UTC, datetime
from fastapi.testclient import TestClient

from skudo.web.app import app, get_db
from skudo.web.auth import create_token
from skudo.mirror.models import Tenant
from skudo.rules.models import Rule, RulesetSnapshot
from skudo.findings.models import Finding, FindingRun


def _client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _auth():
    return {"Authorization": f"Bearer {create_token(1, 'a@x.com', 'administrador')}"}


def test_rules_endpoint_lista_reglas_activas_con_recuento(db_session):
    t = Tenant(code="acme", name="Acme", base_url="http://x.test", token_env_var="X")
    db_session.add(t); db_session.flush()
    r = Rule(tenant_id=t.id, scope_kind="attribute_set", scope_key="4",
             store_view_magento_id=1, axis=3, kind="obligatoriedad",
             definition={"attribute": "color"}, confidence=0.97, evidence_count=100,
             exceptions=[], status="aceptada", origin="inferida")
    db_session.add(r); db_session.flush()
    run = FindingRun(tenant_id=t.id, store_view_magento_id=1, mirror_sync_generation=1,
                     product_count=10, ruleset_version=1, finished_at=datetime.now(UTC))
    db_session.add(run); db_session.flush()
    db_session.add(Finding(run_id=run.id, code="regla:obligatoriedad:color", axis=3,
                           severity="media", subject_type="producto", subject_key="B",
                           evidence={"attribute": "color", "rule_id": r.id},
                           rule_id=r.id, ruleset_version=1))
    db_session.flush()

    client = _client(db_session)
    resp = client.get("/api/tenants/acme/rules?store=1", headers=_auth())
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    data = resp.json()
    fila = next(x for x in data if x["attribute"] == "color")
    assert fila["kind"] == "obligatoriedad"
    assert fila["status"] == "aceptada"
    assert fila["origin"] == "inferida"
    assert fila["confidence"] == 0.97
    assert fila["productos_marcados"] == 1
```

- [ ] **Step 2: Correr y ver que falla**

Run: `python -m pytest tests/web/test_rules_endpoint.py -v`
Expected: FAIL (404 o AttributeError; el endpoint no existe).

- [ ] **Step 3: Agregar el endpoint y enriquecer el informe**

En `src/skudo/web/app.py`, agregar (siguiendo el patrón de los endpoints existentes con `require_user` y `get_db`):

```python
@app.get("/api/tenants/{tenant_code}/rules")
def tenant_rules(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    from skudo.rules.models import Rule
    from skudo.findings.models import Finding, FindingRun

    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    reglas = db.scalars(
        select(Rule).where(
            Rule.tenant_id == tenant.id,
            Rule.status.in_(("aceptada", "aviso", "borrador")),
            (Rule.store_view_magento_id == store) | (Rule.store_view_magento_id.is_(None)),
        ).order_by(Rule.id)
    ).all()

    # recuento de productos marcados por regla en la última pasada de esa store view
    ultima = db.scalars(
        select(FindingRun).where(
            FindingRun.tenant_id == tenant.id,
            FindingRun.store_view_magento_id == store,
            FindingRun.finished_at.is_not(None),
        ).order_by(FindingRun.id.desc()).limit(1)
    ).first()
    marcados: dict[int, int] = {}
    if ultima is not None:
        for rid, n in db.execute(
            select(Finding.rule_id, func.count()).where(
                Finding.run_id == ultima.id, Finding.rule_id.is_not(None)
            ).group_by(Finding.rule_id)
        ).all():
            marcados[rid] = n

    return [
        {
            "id": r.id, "kind": r.kind, "attribute": (r.definition or {}).get("attribute"),
            "scope": f"{r.scope_kind}:{r.scope_key}", "axis": r.axis,
            "confidence": r.confidence, "evidence_count": r.evidence_count,
            "status": r.status, "origin": r.origin,
            "productos_marcados": marcados.get(r.id, 0),
        }
        for r in reglas
    ]
```

En `findings_report` (en `run.py`), agregar a cada hallazgo del informe su origen: si el `code` empieza con `regla:`, marcar `"origen": "regla"` y adjuntar `rule_id`; si no, `"origen": "detector"`. (Es un enriquecimiento del dict que ya arma; no cambia su forma general.)

- [ ] **Step 4: Correr y ver que pasa**

Run: `python -m pytest tests/web/test_rules_endpoint.py -q`
Expected: PASS. Nota: si no existe `tests/web/__init__.py` ni el patrón `TestClient`, crear el `__init__.py`; `fastapi.testclient` viene con fastapi (ya es dependencia). Verificar que `get_db` es importable desde `skudo.web.app`.

- [ ] **Step 5: Ruff y commit**

```bash
ruff check src/skudo/web/app.py src/skudo/findings/run.py tests/web/test_rules_endpoint.py
git add src/skudo/web/app.py src/skudo/findings/run.py tests/web/
git commit -m "feat(web): endpoint /rules y origen (detector/regla) en el informe de hallazgos"
```

---

### Task 7: El panel — vista Reglas y hallazgos por origen

**Files:**
- Modify: `src/skudo/web/static/index.html`

**Interfaces:**
- Consumes: `GET /api/tenants/{code}/rules` (Task 6), el `/findings` enriquecido, y los endpoints existentes.
- Produces: una vista **Reglas** en la SPA; la vista **Hallazgos** distingue origen; un tile de reglas activas en el Dashboard.

**Nota:** REQUIRED — usar la skill **impeccable** para lo visual (tokens, tipografía, tema claro/oscuro), consistente con el `index.html` existente. No inventar un sistema nuevo: seguir los tokens y componentes ya presentes en el archivo.

- [ ] **Step 1: Leer el panel existente y su sistema visual**

Run: leer `src/skudo/web/static/index.html` — sus tokens CSS, cómo arma las vistas (el router `navigate`, los `render*`), la barra lateral, y cómo llama la API. La vista nueva debe calcar ese patrón.

- [ ] **Step 2: Agregar la entrada de navegación "Reglas"**

En el `<nav>` de la barra lateral, agregar un item `data-view="rules"` con un ícono coherente con los demás (SVG stroke), entre "Hallazgos" y "Productos". Registrar el caso en `navigate()`.

- [ ] **Step 3: Implementar `renderRules()`**

Una tabla de reglas activas ordenada por impacto (`severidad × productos_marcados`), con columnas: atributo, kind, scope, origen (badge: inferida / piso_externo / curada), confianza (con barra), evidencia, productos marcados, estado (badge: aceptada / aviso / borrador). Reusar los estilos de tabla y de badge del archivo (`.findings-table`, `.severity-badge`). Las `borrador` se muestran atenuadas con una nota "disponible para curar (S1d)". Llama `GET /api/tenants/{code}/rules?store=S`.

- [ ] **Step 4: Marcar el origen en `renderFindings()`**

En la tabla de hallazgos, agregar una columna/badge "origen" (detector / regla). Un hallazgo de regla muestra, en un tooltip o subtexto, la confianza de su regla. Usar el campo `origen`/`rule_id` que ahora trae `/findings`.

- [ ] **Step 5: Tile de reglas activas en el Dashboard**

En el hero o bajo él, un tile pequeño: "N reglas activas (K aceptadas · M en aviso)", que enlaza a la vista Reglas. Cifra tomada del endpoint `/rules` (contar por status).

- [ ] **Step 6: Verificación visual (una mirada)**

Abrir el `index.html` localmente (o su preview) UNA vez, revisar que las tres piezas se ven coherentes con el resto (tema claro/oscuro, tipografía, espaciado), y hacer una sola pasada de ajustes. No iterar con capturas repetidas.

- [ ] **Step 7: Commit**

```bash
git add src/skudo/web/static/index.html
git commit -m "feat(web): vista Reglas, hallazgos por origen y tile de reglas activas (impeccable)"
```

---

### Task 8: El artifact demo con datos de reglas

**Files:**
- Modify/Create: el HTML demo (copia del panel con datos baked-in de Renovapadel), en el scratchpad para publicar como artifact.

**Interfaces:**
- Consumes: la forma de las vistas de Task 7.
- Produces: un artifact publicado que muestra S1c-1 (reglas + hallazgos por origen) con datos demo realistas, sin backend.

**Nota:** REQUIRED — cargar la skill **artifact-design** antes de escribir el HTML, y publicar con la herramienta Artifact. Datos demo realistas de Renovapadel: salud 85, grado B, 1352 productos, 198 no publicables; y reglas de ejemplo coherentes (p.ej. `regla:obligatoriedad:color` sobre indumentaria con ~120 productos marcados, confianza 0.94, origen inferida, estado aceptada; una `piso_externo` de `size` en aviso; un par de `borrador` disponibles para curar).

- [ ] **Step 1: Cargar artifact-design y construir el HTML demo**

Partir del `index.html` de Task 7, reemplazar las llamadas API por un objeto `DEMO` con datos baked-in que incluya la nueva forma (reglas, hallazgos con origen). Saltar el login, abrir en el Dashboard.

- [ ] **Step 2: Publicar el artifact**

Publicar con la herramienta Artifact (favicon 🛡️, título "Skudo"). Verificar que las cuatro vistas (Dashboard, Reglas, Hallazgos, Productos) se ven sin backend.

- [ ] **Step 3: Registrar el enlace**

Dejar el URL del artifact en el reporte de la tarea; es el entregable visible de S1c-1.

*(Task 8 no tiene tests automatizados: es un artifact de demostración. Su verificación es la mirada visual del Step 2.)*

---

## Self-review del plan

**Cobertura del diseño (§ → task):**
- §3 motor de evaluación → Task 2 ✓
- §3.1 ruleset congelado, §3.2 scope → Task 3 ✓
- §4 atribución en el esquema → Task 1 ✓
- §5 dedup por causa raíz → Task 4 ✓
- §6 CLI evaluate → Task 5 ✓
- §7.1 endpoint /rules + informe → Task 6 ✓
- §7.2 vistas del panel → Task 7 ✓
- §7.3 datos demo → Task 8 ✓
- §10 criterios de aceptación → tests nombrados: "una_regla_aceptada...produce_hallazgo_atribuido" (crit.1), "una_regla_borrador_no_esta_en_el_snapshot" (crit.2), "detector_y_regla...descuentan_una_vez" (crit.3), "desconocido_no_es_carencia" (crit.4), test del endpoint /rules (crit.5) ✓

**Consistencia de tipos:** `evaluar_regla(regla, fichas, sets_by_code)` en Task 2 y llamado igual en Task 3. `ReglaEvaluable` con los mismos campos en 2 y 3. `HallazgoDeProducto(..., causa=)` en Task 4 usado por `score/run.py`. `detect_store_view(session, tenant_id, store, ruleset_version=None)` en Task 3, llamado en Task 5. Coinciden.

**Placeholders:** ninguno de código. Task 7 (panel) y Task 8 (artifact) son visuales y delegan en las skills impeccable/artifact-design el detalle de estilo, con la estructura y los datos especificados — es lo correcto para trabajo de UI, no un placeholder.

**Puntos a verificar en ejecución (declarados, no adivinados):**
- Task 1: si `test_mirror_has_no_orphans` exige clasificar la FK nueva `finding→rule`, ajustarla (nullable = arista opcional).
- Task 2: la firma exacta de `Ficha.valor()` — confirmar en `catalog.py` antes de usar.
- Task 3: `_escribir_resultado` factoriza la escritura que hoy está inline en `detect_store_view`; mover, no duplicar.
- Task 6: existencia de `tests/web/__init__.py` y de `get_db` importable; crearlos/confirmarlos.
