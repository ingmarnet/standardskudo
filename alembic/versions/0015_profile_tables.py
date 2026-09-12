"""profile tables

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Las cuatro tablas del perfil (S1a).

    Viven aparte del espejo a propósito: el espejo es lo único que no se puede
    recalcular sin volver a hablar con el Magento del cliente, y un perfil
    entero se puede tirar y rehacer. Separarlos hace que una migración del
    perfil no pueda tocar por accidente la fidelidad del espejo.
    """
    op.create_table(
        "profile_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("mirror_sync_generation", sa.BigInteger(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
        # NULL mientras la pasada no termina: un sello a medias invita a
        # comparar dos perfiles que no midieron lo mismo.
        sa.Column("digest", sa.String(64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_profile_run_tenant_id", "profile_run", ["tenant_id"])
    op.create_index(
        "ix_profile_run_store_view_magento_id", "profile_run", ["store_view_magento_id"]
    )

    op.create_table(
        "profile_partition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("profile_run.id"), nullable=False
        ),
        # NULL = los productos cuyo attribute set el espejo desconoce.
        sa.Column("attribute_set_id", sa.Integer(), nullable=True),
        sa.Column("splitter_kind", sa.String(32), nullable=False),
        sa.Column("splitter_key", sa.String(255), nullable=True),
        sa.Column("splitter_value", sa.String(255), nullable=True),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column("ambiguity", sa.Float(), nullable=True),
        sa.Column("decision_reason", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "attribute_set_id",
            "splitter_value",
            name="uq_profile_partition_run_set_value",
        ),
    )
    op.create_index("ix_profile_partition_run_id", "profile_partition", ["run_id"])

    op.create_table(
        "profile_attribute_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "partition_id",
            sa.Integer(),
            sa.ForeignKey("profile_partition.id"),
            nullable=False,
        ),
        sa.Column("attribute_code", sa.String(255), nullable=False),
        sa.Column("presente", sa.Integer(), nullable=False),
        sa.Column("vacio", sa.Integer(), nullable=False),
        sa.Column("no_aplica", sa.Integer(), nullable=False),
        sa.Column("desconocido", sa.Integer(), nullable=False),
        # NULL y 0.0 son cosas distintas: "no se pudo medir" contra "no lo tiene
        # nadie". La columna tiene que poder decirlo.
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.UniqueConstraint(
            "partition_id", "attribute_code", name="uq_profile_coverage_particion_attr"
        ),
    )
    op.create_index(
        "ix_profile_attribute_coverage_partition_id",
        "profile_attribute_coverage",
        ["partition_id"],
    )

    op.create_table(
        "profile_value_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "partition_id",
            sa.Integer(),
            sa.ForeignKey("profile_partition.id"),
            nullable=False,
        ),
        sa.Column("attribute_code", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("n_present", sa.Integer(), nullable=False),
        # Valores que no se pudieron leer como número sin adivinar. Se cuentan
        # aparte en vez de descartarse: son el insumo del detector de sospecha
        # de conversión de S1c.
        sa.Column("n_ambiguous", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("minimum", sa.Float(), nullable=True),
        sa.Column("p05", sa.Float(), nullable=True),
        sa.Column("p50", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.Column("maximum", sa.Float(), nullable=True),
        sa.Column("distinct_values", sa.Integer(), nullable=True),
        sa.Column("mode_share", sa.Float(), nullable=True),
        sa.Column("discriminating_power", sa.Float(), nullable=True),
        sa.Column("top_values", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "partition_id", "attribute_code", name="uq_profile_stats_particion_attr"
        ),
    )
    op.create_index(
        "ix_profile_value_stats_partition_id", "profile_value_stats", ["partition_id"]
    )


def downgrade() -> None:
    op.drop_table("profile_value_stats")
    op.drop_table("profile_attribute_coverage")
    op.drop_table("profile_partition")
    op.drop_table("profile_run")
