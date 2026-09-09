"""unknown commercial signals

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09 14:16:03.771845

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 'Desconocido' no es 'cero'. `salable_qty` y `margin` ya lo admitían; estas
    # tres son igual de medidas y estaban colapsando la laguna del origen en un
    # cero de aspecto confiable.
    op.alter_column('product_signal', 'units_sold',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.alter_column('product_signal', 'revenue',
               existing_type=sa.NUMERIC(precision=18, scale=4),
               nullable=True)
    op.alter_column('product_signal', 'search_demand',
               existing_type=sa.INTEGER(),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema.

    Falla si ya hay desconocidos guardados, y debe fallar: volver a NOT NULL
    obligaría a inventar un cero para cada laguna, que es exactamente el dato
    falso que esta migración vino a eliminar. Quien necesite bajar tiene que
    decidir explícitamente qué hacer con esas filas.
    """
    op.alter_column('product_signal', 'search_demand',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.alter_column('product_signal', 'revenue',
               existing_type=sa.NUMERIC(precision=18, scale=4),
               nullable=False)
    op.alter_column('product_signal', 'units_sold',
               existing_type=sa.INTEGER(),
               nullable=False)
