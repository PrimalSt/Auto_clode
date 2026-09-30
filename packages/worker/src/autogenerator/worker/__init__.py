"""Код исполнителя: собирает модули обработки в задания ``run``, ``validate``, ``inspect``
(ARCHITECTURE.md, раздел 6.6). Модули обработки вместе импортирует только этот пакет."""

from .run import ManifestHistory, output_path, run, validate
from .tools import inspect, load_scenario, plugin_manifest

__all__ = [
    "ManifestHistory",
    "inspect",
    "load_scenario",
    "output_path",
    "plugin_manifest",
    "run",
    "validate",
]
