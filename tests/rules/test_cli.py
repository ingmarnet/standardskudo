"""El CLI de reglas, ejercitando cli.main() de verdad contra la base de tests.

main() abre su propia sesión contra SKUDO_DATABASE_URL, así que estas pruebas
NO usan el db_session de rollback: usan el fixture cli_db (que apunta la variable
a migrated_engine y trunca al terminar) y siembran con una sesión aparte que
hace COMMIT, porque la sesión de main() es otra transacción.
"""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo import cli
from skudo.mirror.models import Tenant
from skudo.profile.models import AttributeCoverage, ProfilePartition, ProfileRun
from skudo.rules.models import Rule, RuleVersion

# Reusa el fixture cli_db, movido a tests/conftest.py como parte de esta tarea
# (junto con las constantes TOKEN/TOKEN_VAR que necesita) para que este módulo
# lo herede sin duplicarlo.


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


def test_rules_infer_dos_veces_no_rompe_por_fk_de_rule_version(cli_db, capsys):
    """Carry-forward de Task 3: re-inferir borraba Rule sin borrar antes sus
    RuleVersion, y rule_version.rule_id no tiene ON DELETE CASCADE. Un segundo
    `rules infer` sobre el mismo perfil debe limpiar ambas tablas y no fallar,
    dejando sólo una generación de reglas (no reglas duplicadas)."""
    tid = _sembrar_perfil(cli_db)
    assert cli.main(["rules", "infer", "--tenant", "acme", "--store", "1"]) == 0
    with Session(cli_db) as s:
        primera_generacion = s.query(Rule).filter_by(tenant_id=tid).all()
        assert primera_generacion
        rid = primera_generacion[0].id
    # Aceptar una regla escribe un RuleVersion; así el segundo infer tiene que
    # borrar una fila de rule_version además de la de rule.
    assert cli.main(["rules", "accept", str(rid), "--actor", "ana@x.com"]) == 0
    with Session(cli_db) as s:
        assert s.query(RuleVersion).filter_by(rule_id=rid).count() >= 1

    assert cli.main(["rules", "infer", "--tenant", "acme", "--store", "1"]) == 0

    with Session(cli_db) as s:
        segunda_generacion = s.query(Rule).filter_by(tenant_id=tid).all()
        assert len(segunda_generacion) == len(primera_generacion)
        assert all(r.status == "borrador" for r in segunda_generacion)
        # Ninguna RuleVersion apunta a un id de la primera generación: se
        # borraron junto con sus reglas.
        ids_primera_generacion = {r.id for r in primera_generacion}
        assert not (
            set(s.scalars(select(RuleVersion.rule_id)).all()) & ids_primera_generacion
        )
