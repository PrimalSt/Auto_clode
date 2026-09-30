"""Небольшие задания для CLI и интерфейса: снимок структуры файла и манифест плагинов."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autogenerator.contracts import PluginManifest, ReadOptions, ScenarioSpec, SchemaSnapshot


def inspect(path: str | Path, options: ReadOptions | None = None, fmt: str | None = None) -> SchemaSnapshot:
    from autogenerator.ingest import inspect_file
    from autogenerator.plugin_host import PluginRegistry

    return inspect_file(path, PluginRegistry.discover(), options, fmt)


def plugin_manifest(isolated: bool = True) -> PluginManifest:
    """Манифест плагинов; по умолчанию — из отдельного процесса, как его получает сервер."""
    from autogenerator.plugin_host import PluginRegistry, discover_manifest_isolated

    return discover_manifest_isolated() if isolated else PluginRegistry.discover().manifest()


def load_scenario(data: Any, where: str = "сценарий") -> ScenarioSpec:
    from autogenerator.engine import load_scenario as _load

    return _load(data, where)
