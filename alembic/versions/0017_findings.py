"""findings

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-16 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finding_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("mirror_sync_generation", sa.BigInteger(), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_finding_run_tenant_id", "finding_run", ["tenant_id"])
    op.create_index("ix_finding_run_store_view_magento_id", "finding_run",
                    ["store_view_magento_id"])

    op.create_table(
        "finding",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("finding_run.id"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("axis", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
    )
    op.create_index("ix_finding_run_id", "finding", ["run_id"])
    op.create_index("ix_finding_code", "finding", ["code"])

    op.create_table(
        "finding_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("finding_run.id"), nullable=False),
        sa.Column("detector", sa.String(64), nullable=False),
        sa.Column("evaluados", sa.Integer(), nullable=False),
        sa.Column("no_aplica", sa.Integer(), nullable=False),
        sa.Column("no_evaluado", sa.Integer(), nullable=False),
        sa.Column("motivo_no_aplica", sa.String(512), nullable=False, server_default=""),
        sa.UniqueConstraint("run_id", "detector", name="uq_finding_coverage_run_detector"),
    )
    op.create_index("ix_finding_coverage_run_id", "finding_coverage", ["run_id"])


def downgrade() -> None:
    op.drop_table("finding_coverage")
    op.drop_table("finding")
    op.drop_table("finding_run")
