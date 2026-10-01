"""Код исполнителя: собирает модули обработки в задания ``run``, ``validate``, ``ingest``,
``inspect`` (ARCHITECTURE.md, раздел 6.6). Модули обработки вместе импортирует только этот пакет."""

from .ingest_job import draft_source, ingest_upload
from .run import ManifestHistory, output_path, run, validate
from .tools import (
    coverage_report,
    default_period,
    export_history,
    history_frame,
    inspect,
    load_scenario,
    plugin_manifest,
    rows_outside,
)

__all__ = [
    "ManifestHistory",
    "coverage_report",
    "default_period",
    "draft_source",
    "export_history",
    "history_frame",
    "ingest_upload",
    "inspect",
    "load_scenario",
    "output_path",
    "plugin_manifest",
    "rows_outside",
    "run",
    "validate",
]
