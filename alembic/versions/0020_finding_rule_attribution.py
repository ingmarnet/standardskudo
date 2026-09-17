"""finding rule attribution

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("finding_run", sa.Column("ruleset_version", sa.Integer(), nullable=True))
    op.add_column("finding", sa.Column("rule_id", sa.Integer(),
                                       sa.ForeignKey("rule.id"), nullable=True))
    op.add_column("finding", sa.Column("ruleset_version", sa.Integer(), nullable=True))
    op.create_index("ix_finding_rule_id", "finding", ["rule_id"])


def downgrade() -> None:
    op.drop_index("ix_finding_rule_id", "finding")
    op.drop_column("finding", "ruleset_version")
    op.drop_column("finding", "rule_id")
    op.drop_column("finding_run", "ruleset_version")
