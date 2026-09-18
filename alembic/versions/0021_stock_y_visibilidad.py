"""stock y visibilidad

Revision ID: 0021
Revises: 0020
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_signal", sa.Column("is_in_stock", sa.Boolean(), nullable=True))
    op.add_column("product_score", sa.Column("prioridad_vitrina", sa.String(length=16), nullable=True))
    op.create_table(
        "store_setting",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False, index=True),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False, index=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "store_view_magento_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("store_setting")
    op.drop_column("product_score", "prioridad_vitrina")
    op.drop_column("product_signal", "is_in_stock")
