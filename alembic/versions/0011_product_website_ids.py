"""product website ids

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-09 23:06:47.677735

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # El payload de producto ya trae `website_ids` (Task A3); sin esta columna,
    # `derive_category_effect` no puede evaluar su tercera condición, que es la
    # única que distingue PY de BR en el tenant piloto (comparten
    # root_category_id). Nullable: una fila que ningún camino de sync haya
    # tocado todavía no debe fingir un cero o una lista vacía con aspecto
    # confiable.
    op.add_column(
        "product_record", sa.Column("website_ids", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("product_record", "website_ids")
