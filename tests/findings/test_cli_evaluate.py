from sqlalchemy.orm import Session

from skudo import cli
from skudo.findings.models import Finding, FindingRun
from skudo.mirror.models import Attribute, ProductRecord, Tenant
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
