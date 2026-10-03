"""Код исполнителя: собирает модули обработки в задания ``run``, ``validate``, ``ingest``,
``inspect``, ``import_theme``, ``slide_image`` (ARCHITECTURE.md, раздел 6.6). Модули обработки
вместе импортирует только этот пакет."""

from .ingest_job import draft_source, ingest_upload
from .run import ManifestHistory, output_path, preview, run, validate
from .theme_jobs import describe_theme, import_theme, scaffold_theme, slide_image
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
    "describe_theme",
    "draft_source",
    "export_history",
    "history_frame",
    "import_theme",
    "ingest_upload",
    "inspect",
    "load_scenario",
    "output_path",
    "plugin_manifest",
    "preview",
    "rows_outside",
    "run",
    "scaffold_theme",
    "slide_image",
    "validate",
]
