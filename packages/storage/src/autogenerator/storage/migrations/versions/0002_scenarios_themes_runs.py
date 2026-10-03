"""Сценарии, шаблоны оформления и запуски с их версиями (этап M4).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "themes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("current_version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "theme_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("theme_id", sa.String(64), sa.ForeignKey("themes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("pptx_uri", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_name", sa.Text, nullable=False),
        sa.Column("manifest", sa.JSON, nullable=False),
        sa.Column("roles", sa.JSON, nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("theme_id", "number", name="uq_theme_versions_number"),
    )
    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("current_version", sa.Integer, nullable=False),
        sa.Column("last_run_seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("scenario_id", sa.String(64), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("text", sa.Text),
        sa.Column("theme_id", sa.String(64)),
        sa.Column("theme_version", sa.Integer),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scenario_id", "number", name="uq_scenario_versions_number"),
    )
    op.create_table(
        "scenario_inputs",
        sa.Column("scenario_id", sa.String(64), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("input_id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("is_main", sa.Boolean, nullable=False),
    )
    op.create_index("ix_scenario_inputs_source", "scenario_inputs", ["source_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("scenario_id", sa.String(64), sa.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("scenario_version", sa.Integer, nullable=False),
        sa.Column("scenario_name", sa.Text, nullable=False),
        sa.Column("theme_id", sa.String(64)),
        sa.Column("theme_version", sa.Integer),
        sa.Column("period", sa.JSON),
        sa.Column("period_given", sa.Boolean, nullable=False),
        sa.Column("source_versions", sa.JSON, nullable=False),
        sa.Column("inputs_history", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON),
        sa.Column("output_uri", sa.Text),
        sa.Column("output_copy", sa.Text),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("scenario_id", "seq", name="uq_runs_seq"),
    )
    op.create_index("ix_runs_started", "runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_started", "runs")
    op.drop_table("runs")
    op.drop_index("ix_scenario_inputs_source", "scenario_inputs")
    op.drop_table("scenario_inputs")
    op.drop_table("scenario_versions")
    op.drop_table("scenarios")
    op.drop_table("theme_versions")
    op.drop_table("themes")
