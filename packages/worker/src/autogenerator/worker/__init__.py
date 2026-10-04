"""Код исполнителя: собирает модули обработки в задания ``run``, ``validate``, ``ingest``,
``inspect``, ``import_theme``, ``slide_image`` (ARCHITECTURE.md, раздел 6.6). Модули обработки
вместе импортирует только этот пакет."""

from .ingest_job import check_file, draft_source, ingest_upload, mapping_question, normalize_name, with_aliases
from .run import ManifestHistory, output_path, preview, run, validate
from .theme_jobs import describe_theme, import_theme, scaffold_theme, slide_image
from .tools import (
    column_usage,
    coverage_report,
    default_period,
    environment_info,
    export_history,
    history_frame,
    inspect,
    load_scenario,
    plugin_manifest,
    rows_outside,
    suggest_id,
)


def restart_requested() -> bool:
    """Перезапустить ли процесс-исполнитель после вызова: в нём выполнялся код пользователя
    (``runner.ProcessExecutor`` спрашивает после каждого вызова)."""
    import sys

    usercode = sys.modules.get("autogenerator.engine.usercode")
    return usercode is not None and bool(usercode.ran_in_process())


__all__ = [
    "ManifestHistory",
    "check_file",
    "column_usage",
    "coverage_report",
    "default_period",
    "describe_theme",
    "draft_source",
    "environment_info",
    "export_history",
    "history_frame",
    "import_theme",
    "ingest_upload",
    "inspect",
    "load_scenario",
    "mapping_question",
    "normalize_name",
    "output_path",
    "plugin_manifest",
    "preview",
    "restart_requested",
    "rows_outside",
    "run",
    "scaffold_theme",
    "slide_image",
    "suggest_id",
    "validate",
    "with_aliases",
]
