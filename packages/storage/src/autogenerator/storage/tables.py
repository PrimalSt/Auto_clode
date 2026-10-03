"""Таблицы метаданных (ARCHITECTURE.md, раздел 9). Схема меняется только миграциями
Alembic (``migrations/versions``); этот файл описывает текущее состояние для запросов."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

sources = Table(
    "sources",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("workspace_id", String(64), nullable=False),
    Column("owner_id", String(64)),
    Column("name", Text, nullable=False),
    Column("current_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

source_versions = Table(
    "source_versions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", String(64), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("spec", JSON, nullable=False),
    Column("comment", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_id", "number", name="uq_source_versions_number"),
)

uploads = Table(
    "uploads",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source_id", String(64), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("source_version", Integer, nullable=False),
    Column("original_name", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("format", String(16), nullable=False),
    Column("options", JSON),
    Column("raw_uri", Text),
    Column("data_uri", Text, nullable=False),
    Column("rejects_uri", Text),
    Column("schema_snapshot", JSON),
    Column("cast_report", JSON, nullable=False),
    Column("mapping", JSON, nullable=False),
    Column("profile", JSON, nullable=False),
    Column("period_start", Date, nullable=False),
    Column("period_end_exclusive", Date, nullable=False),
    Column("period_unit", String(16), nullable=False),
    Column("period_label", String(32), nullable=False),
    Column("period_from_data", JSON),
    Column("rows", BigInteger, nullable=False),
    Column("rows_outside_period", BigInteger, nullable=False, default=0),
    Column("null_period_rows", BigInteger, nullable=False, default=0),
    Column("status", String(16), nullable=False),
    Column("review_reasons", JSON, nullable=False),
    Column("overlap_policy", String(16)),
    Column("data_bytes", BigInteger, nullable=False, default=0),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("source_id", "seq", name="uq_uploads_seq"),
)

themes = Table(
    "themes",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("workspace_id", String(64), nullable=False),
    Column("owner_id", String(64)),
    Column("name", Text, nullable=False),
    Column("current_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

theme_versions = Table(
    "theme_versions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("theme_id", String(64), ForeignKey("themes.id", ondelete="CASCADE"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("pptx_uri", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("original_name", Text, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("comment", Text, nullable=False, default=""),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("theme_id", "number", name="uq_theme_versions_number"),
)

scenarios = Table(
    "scenarios",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("workspace_id", String(64), nullable=False),
    Column("owner_id", String(64)),
    Column("name", Text, nullable=False),
    Column("current_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

scenario_versions = Table(
    "scenario_versions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scenario_id", String(64), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("spec", JSON, nullable=False),
    Column("text", Text),
    Column("theme_id", String(64)),
    Column("theme_version", Integer),
    Column("comment", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("scenario_id", "number", name="uq_scenario_versions_number"),
)

scenario_inputs = Table(
    "scenario_inputs",
    metadata,
    Column("scenario_id", String(64), ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True),
    Column("input_id", String(64), primary_key=True),
    Column("source_id", String(64), nullable=False),
    Column("is_main", Boolean, nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", String(80), primary_key=True),
    Column("scenario_id", String(64), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("scenario_version", Integer, nullable=False),
    Column("scenario_name", Text, nullable=False),
    Column("theme_id", String(64)),
    Column("theme_version", Integer),
    Column("period", JSON),
    Column("period_given", Boolean, nullable=False),
    Column("source_versions", JSON, nullable=False),
    Column("inputs_history", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("result", JSON),
    Column("output_uri", Text),
    Column("output_copy", Text),
    Column("trigger", String(16), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("scenario_id", "seq", name="uq_runs_seq"),
)
