"""Публичный фасад Autogenerator для своего кода и Jupyter: ``load_scenario``, ``run``,
``preview``, ``preview_slide``, ``validate``, ``inspect``, ``modules``, шаблон оформления
(``check_theme``, ``describe_theme``, ``scaffold_theme``) и папка данных ``Home`` (источники,
загрузки, история)."""

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
from .home import Home, UploadOutcome

__all__ = [
    "Home",
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
