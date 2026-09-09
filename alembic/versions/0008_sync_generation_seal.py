"""sync generation seal

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09 14:05:11.412330

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # La secuencia se crea a mano porque no cuelga de la metadata de los
    # modelos: `full_sync` la consume con nextval() y nada la referencia como
    # default de columna, así que --autogenerate no la ve ni la reclama.
    op.execute("CREATE SEQUENCE product_sync_generation_seq AS bigint")
    # server_default 0 para que las filas preexistentes queden explícitamente
    # sin sellar (0 no lo devuelve nunca la secuencia, que empieza en 1) y la
    # primera pasada completa las barra si el origen ya no las ofrece.
    op.add_column(
        'product_record',
        sa.Column(
            'sync_generation', sa.BigInteger(), server_default=sa.text('0'), nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('product_record', 'sync_generation')
    op.execute("DROP SEQUENCE product_sync_generation_seq")
