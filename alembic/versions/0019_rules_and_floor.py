"""rules and floor

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-16 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=True),
        sa.Column("axis", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("exceptions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("false_positive_rate", sa.Float(), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("profile_run_id", sa.Integer(), sa.ForeignKey("profile_run.id"), nullable=True),
        sa.Column("ruleset_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    for col in ("tenant_id", "status", "origin", "profile_run_id"):
        op.create_index(f"ix_rule_{col}", "rule", [col])

    op.create_table(
        "rule_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("rule.id"), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(320), nullable=False),
        sa.Column("motivo", sa.String(1024), nullable=False, server_default=""),
        sa.Column("definition_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rule_version_rule_id", "rule_version", ["rule_id"])

    op.create_table(
        "ruleset_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("store_view_magento_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rule_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "store_view_magento_id", "version",
                            name="uq_ruleset_snapshot_tenant_store_version"),
    )
    op.create_index("ix_ruleset_snapshot_tenant_id", "ruleset_snapshot", ["tenant_id"])

    op.create_table(
        "google_floor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_group", sa.String(255), nullable=False),
        sa.Column("google_category_min", sa.Integer(), nullable=True),
        sa.Column("google_category_max", sa.Integer(), nullable=True),
        sa.Column("google_attribute", sa.String(64), nullable=False),
        sa.Column("requirement", sa.String(16), nullable=False),
        sa.Column("axis", sa.Integer(), nullable=False),
        sa.Column("applicability", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(512), nullable=False, server_default=""),
    )

    op.create_table(
        "concept_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.id"), nullable=False),
        sa.Column("canonical", sa.String(64), nullable=False),
        sa.Column("attribute_code", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.UniqueConstraint("tenant_id", "canonical", "attribute_code",
                            name="uq_concept_map_tenant_canonical_attr"),
    )
    op.create_index("ix_concept_map_tenant_id", "concept_map", ["tenant_id"])
    op.create_index("ix_concept_map_canonical", "concept_map", ["canonical"])


def downgrade() -> None:
    op.drop_table("concept_map")
    op.drop_table("google_floor")
    op.drop_table("ruleset_snapshot")
    op.drop_table("rule_version")
    op.drop_table("rule")
