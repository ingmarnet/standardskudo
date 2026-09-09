"""unknown source timestamp

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-09 14:24:47.905112

"""

from collections.abc import Sequence

from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Los catálogos Magento heredados traen '0000-00-00 00:00:00' y cadenas
    # vacías en `updated_at`. Guardar NULL dice "el origen no informó cuándo
    # cambió"; una fecha de relleno sería un dato falso con aspecto confiable.
    op.alter_column('product_record', 'magento_updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=True)


def downgrade() -> None:
    """Downgrade schema.

    Falla si ya hay registros con la fecha desconocida, y debe fallar: volver a
    NOT NULL obligaría a inventar una fecha por cada uno.
    """
    op.alter_column('product_record', 'magento_updated_at',
               existing_type=postgresql.TIMESTAMP(timezone=True),
               nullable=False)
