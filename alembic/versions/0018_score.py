"""score

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-16 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dos formas distintas a propósito: estado actual por producto, historia
    por catálogo. Ver `skudo/score/models.py` para el porqué."""
    op.create_table(
        "product_score",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("sku", sa.String(255), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("puntaje", sa.SmallInteger(), nullable=False),
        sa.Column("grado", sa.String(1), nullable=False),
        sa.Column("critico", sa.Boolean(), nullable=False),
        sa.Column("deducciones", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("finding_run.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "sku", "store_view_magento_id",
                            name="uq_product_score_tenant_sku_store"),
    )
    for col in ("tenant_id", "sku", "store_view_magento_id", "grado", "critico", "run_id"):
        op.create_index(f"ix_product_score_{col}", "product_score", [col])

    op.create_table(
        "catalog_score",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("finding_run.id"),
                  nullable=False, unique=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("salud", sa.SmallInteger(), nullable=False),
        sa.Column("grado", sa.String(1), nullable=False),
        sa.Column("productos", sa.Integer(), nullable=False),
        sa.Column("criticos", sa.Integer(), nullable=False),
        sa.Column("distribucion", sa.JSON(), nullable=False),
        sa.Column("por_eje", sa.JSON(), nullable=False),
        sa.Column("medido_en", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    for col in ("tenant_id", "store_view_magento_id", "medido_en"):
        op.create_index(f"ix_catalog_score_{col}", "catalog_score", [col])
    op.create_index("ix_catalog_score_run_id", "catalog_score", ["run_id"], unique=True)


def downgrade() -> None:
    op.drop_table("catalog_score")
    op.drop_table("product_score")
