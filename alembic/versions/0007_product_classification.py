"""product classification

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09 13:58:26.900368

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable a propósito: la ausencia del dato es 'desconocido', no un id 0.
    op.add_column('product_record', sa.Column('attribute_set_id', sa.Integer(), nullable=True))
    op.add_column('product_record', sa.Column('type_id', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('product_record', 'type_id')
    op.drop_column('product_record', 'attribute_set_id')
