"""arnés de falsos positivos: etiquetas humanas sobre hallazgos

Revision ID: 0022
Revises: 0021
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_label",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False, index=True),
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("finding.id"), nullable=False, index=True),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("labeled_by", sa.String(length=320), nullable=False),
        sa.Column("labeled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("finding_id"),
    )


def downgrade() -> None:
    op.drop_table("finding_label")
