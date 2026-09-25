"""user tenant access

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-24 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_user_tenant_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("platform_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "tenant_id"),
    )
    op.create_index(
        "ix_platform_user_tenant_access_user_id",
        "platform_user_tenant_access",
        ["user_id"],
    )
    op.create_index(
        "ix_platform_user_tenant_access_tenant_id",
        "platform_user_tenant_access",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_user_tenant_access_tenant_id", table_name="platform_user_tenant_access")
    op.drop_index("ix_platform_user_tenant_access_user_id", table_name="platform_user_tenant_access")
    op.drop_table("platform_user_tenant_access")
