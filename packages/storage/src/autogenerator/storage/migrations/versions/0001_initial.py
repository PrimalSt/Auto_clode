"""Источники, версии их настроек и загрузки (этап M1).

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("current_version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "number", name="uq_source_versions_number"),
    )
    op.create_table(
        "uploads",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), sa.ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("source_version", sa.Integer, nullable=False),
        sa.Column("original_name", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("options", sa.JSON),
        sa.Column("raw_uri", sa.Text),
        sa.Column("data_uri", sa.Text, nullable=False),
        sa.Column("rejects_uri", sa.Text),
        sa.Column("schema_snapshot", sa.JSON),
        sa.Column("cast_report", sa.JSON, nullable=False),
        sa.Column("mapping", sa.JSON, nullable=False),
        sa.Column("profile", sa.JSON, nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end_exclusive", sa.Date, nullable=False),
        sa.Column("period_unit", sa.String(16), nullable=False),
        sa.Column("period_label", sa.String(32), nullable=False),
        sa.Column("period_from_data", sa.JSON),
        sa.Column("rows", sa.BigInteger, nullable=False),
        sa.Column("rows_outside_period", sa.BigInteger, nullable=False),
        sa.Column("null_period_rows", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("review_reasons", sa.JSON, nullable=False),
        sa.Column("overlap_policy", sa.String(16)),
        sa.Column("data_bytes", sa.BigInteger, nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "seq", name="uq_uploads_seq"),
    )
    op.create_index("ix_uploads_source", "uploads", ["source_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_uploads_source", "uploads")
    op.drop_table("uploads")
    op.drop_table("source_versions")
    op.drop_table("sources")
