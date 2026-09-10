"""sync pass and catalog seals

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-10 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Las cuatro tablas que hasta M3 no tenían barrido: `sync_attributes` y
# `sync_categories` documentaban "no hay barrido" como fuera de alcance, así
# que una opción, una etiqueta, una categoría o un estado por tienda que el
# origen borraba seguía pareciendo vivo en el espejo para siempre.
SEALED_TABLES = ("attribute", "attribute_option", "category", "category_store_state")


def upgrade() -> None:
    """Upgrade schema."""
    # M3. El equivalente de `full_sync_checkpoint` para las pasadas que NO son
    # por store view: atributos y categorías. Una fila por (tenant, tipo de
    # pasada).
    #
    # No lleva `next_cursor`, y la ausencia es deliberada: estas dos pasadas
    # arrancan SIEMPRE desde la primera página, así que una interrumpida no se
    # reanuda sino que se repite completa con una generación nueva, y todo lo
    # vivo se vuelve a sellar. Eso es lo que hace innecesario —y por tanto
    # inexistente— el cursor persistido que `full_sync` sí necesita.
    #
    # `pass_complete` arranca en false: una fila recién creada no autoriza a
    # borrar nada. Es la precondición del barrido, y se lee de la BASE.
    op.create_table(
        "sync_pass",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        # 'attributes' | 'categories'. Texto y no enum: agregar una pasada no
        # debería exigir una migración de tipo.
        sa.Column("pass_kind", sa.String(32), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("pages_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "items_written", sa.Integer(), server_default=sa.text("0"), nullable=False
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
        sa.UniqueConstraint("tenant_id", "pass_kind"),
    )
    op.create_index("ix_sync_pass_tenant_id", "sync_pass", ["tenant_id"])

    # Mismo sello y misma razón que `product_record.sync_generation` (0008):
    # server_default 0 para que las filas preexistentes queden explícitamente
    # SIN sellar —0 no lo devuelve nunca la secuencia, que empieza en 1— y la
    # primera pasada completa las barra si el origen ya no las ofrece.
    #
    # `attribute_option_label` no lleva sello a propósito: `upsert_option`
    # reemplaza el juego completo de etiquetas de la opción en cada pasada, así
    # que una etiqueta solo puede quedar huérfana si su OPCIÓN desaparece, y el
    # barrido de opciones se las lleva con ella. Un sello propio sería un
    # segundo criterio para la misma decisión.
    for table in SEALED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "sync_generation",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in reversed(SEALED_TABLES):
        op.drop_column(table, "sync_generation")
    op.drop_index("ix_sync_pass_tenant_id", table_name="sync_pass")
    op.drop_table("sync_pass")
