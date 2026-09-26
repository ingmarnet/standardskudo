"""user tenant access role

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-24 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_user_tenant_access",
        sa.Column("role", sa.String(32), nullable=False, server_default="lector"),
    )


def downgrade() -> None:
    op.drop_column("platform_user_tenant_access", "role")
