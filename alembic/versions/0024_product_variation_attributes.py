"""product variation attributes

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-22 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # El payload de producto ya trae `variation_attributes` (los ejes de
    # variación del configurable, desde `catalog_product_super_attribute`); sin
    # esta columna, el detector `variantes_sin_atributos_de_variacion` (Eje 1)
    # no tiene contra qué comparar. Nullable por la misma razón que `parent_skus`.
    op.add_column(
        "product_record", sa.Column("variation_attributes", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("product_record", "variation_attributes")
