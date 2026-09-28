"""evidence

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("datum", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("fragment", sa.String(2000), nullable=False),
        sa.Column("product_sku", sa.String(255), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("transformation", sa.String(32), nullable=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "conflict_state",
            sa.String(16),
            nullable=False,
            server_default="sin_conflicto",
        ),
        sa.Column("conflict_group", sa.String(64), nullable=True),
    )
    op.create_index("ix_evidence_tenant_id", "evidence", ["tenant_id"])
    op.create_index("ix_evidence_subject", "evidence", ["subject_type", "subject_id"])
    op.create_index("ix_evidence_conflict_group", "evidence", ["conflict_group"])


def downgrade() -> None:
    op.drop_index("ix_evidence_conflict_group", table_name="evidence")
    op.drop_index("ix_evidence_subject", table_name="evidence")
    op.drop_index("ix_evidence_tenant_id", table_name="evidence")
    op.drop_table("evidence")
