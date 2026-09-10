"""last delta read at

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # El endpoint `/deltas` acepta `sinceTimestamp` desde S0 Task 10 y nada lo
    # enviaba nunca: no había dónde guardar "hasta cuándo leímos". Con Staging
    # activo, una actualización programada se vuelve activa cuando pasa su
    # `created_in` y en ese instante NO ocurre ningún evento de Magento, así
    # que la cola de cambios no se entera y el espejo sirve el valor viejo
    # indefinidamente. Esta columna es el watermark de tiempo que hacía falta.
    #
    # Nullable a propósito, y sin server_default: NULL significa "este tenant
    # nunca leyó deltas con ventana de tiempo todavía", que no es lo mismo que
    # "leímos hasta la época Unix". Un 0 haría que la primera pasada pidiera
    # las activaciones desde el principio de los tiempos y devolviera la
    # versión activa del catálogo ENTERO como si se acabara de activar.
    op.add_column(
        "sync_watermark",
        sa.Column("last_delta_read_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sync_watermark", "last_delta_read_at")
