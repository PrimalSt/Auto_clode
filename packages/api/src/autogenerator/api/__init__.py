"""Публичный фасад Autogenerator для своего кода и Jupyter: ``load_scenario``, ``run``,
``preview``, ``preview_slide``, ``validate``, ``inspect``, ``modules``, шаблон оформления
(``check_theme``, ``describe_theme``, ``scaffold_theme``) и папка данных ``Home`` (источники,
загрузки, история, сопоставление столбцов, сценарии, шаблоны, запуски, резервные копии)."""

from .facade import (
    check_theme,
    describe_theme,
    find_inputs,
    inspect,
    load_scenario,
    load_sources,
    modules,
    preview,
    preview_slide,
    run,
    scaffold_theme,
    validate,
)
from .home import ChooseMapping, ColumnUsage, Home, MappingChoice, UploadOutcome
from .library import SavedScenario, ThemeImport
from .runs import BackupInfo

__all__ = [
    "BackupInfo",
    "ChooseMapping",
    "ColumnUsage",
    "Home",
    "MappingChoice",
    "SavedScenario",
    "ThemeImport",
    "UploadOutcome",
    "check_theme",
    "describe_theme",
    "find_inputs",
    "inspect",
    "load_scenario",
    "load_sources",
    "modules",
    "preview",
    "preview_slide",
    "run",
    "scaffold_theme",
    "validate",
]
