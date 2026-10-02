"""Публичный фасад Autogenerator для своего кода и Jupyter: ``load_scenario``, ``run``,
``preview``, ``validate``, ``inspect``, ``modules`` и папка данных ``Home`` (источники, загрузки, история)."""

from .facade import find_inputs, inspect, load_scenario, load_sources, modules, preview, run, validate
from .home import Home, UploadOutcome

__all__ = [
    "Home",
    "UploadOutcome",
    "find_inputs",
    "inspect",
    "load_scenario",
    "load_sources",
    "modules",
    "preview",
    "run",
    "validate",
]
