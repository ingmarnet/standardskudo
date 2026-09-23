"""product parent skus

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # El payload de producto ya trae `parent_skus` (link configurable→variante
    # desde `catalog_product_super_link`); sin esta columna, el detector
    # `configurable_sin_hijos` (Eje 1) no tiene contra qué comparar. Nullable
    # por la misma razón que `website_ids`: una fila que ningún camino de sync
    # tocó todavía no debe fingir una lista vacía con aspecto confiable.
    op.add_column(
        "product_record", sa.Column("parent_skus", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("product_record", "parent_skus")
