"""full sync checkpoint

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # H3: `full_sync` hacía UN commit al final de todo el catálogo. Esta tabla
    # es el punto de reanudación: se escribe en la MISMA transacción que la
    # página que acaba de aplicarse, así que el cursor y los datos avanzan
    # juntos o no avanzan.
    #
    # `generation` NO tiene default: una fila sin generación no sabría con qué
    # sello continuar, y continuar con el sello equivocado es exactamente el
    # fallo que haría que el barrido borrara lo que la pasada interrumpida ya
    # escribió.
    #
    # `pass_complete` es la precondición del barrido y arranca en false: una
    # fila recién creada no autoriza a borrar nada.
    op.create_table(
        "full_sync_checkpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("next_cursor", sa.String(512), nullable=True),
        sa.Column("pages_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "records_written", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "pass_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("swept", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "store_view_magento_id"),
    )
    op.create_index(
        "ix_full_sync_checkpoint_tenant_id", "full_sync_checkpoint", ["tenant_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_full_sync_checkpoint_tenant_id", table_name="full_sync_checkpoint")
    op.drop_table("full_sync_checkpoint")
