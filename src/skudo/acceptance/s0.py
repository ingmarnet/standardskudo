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
