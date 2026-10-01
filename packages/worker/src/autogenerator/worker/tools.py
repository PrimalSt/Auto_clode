"""Небольшие задания для CLI и интерфейса: снимок структуры файла, манифест плагинов,
покрытие и действующая история источника."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from autogenerator.contracts import (
    CoverageReport,
    HistoryManifest,
    Period,
    PluginManifest,
    ReadOptions,
    ScenarioSpec,
    SchemaSnapshot,
    SourceSpec,
)

if TYPE_CHECKING:
    import polars as pl


def inspect(
    path: str | Path, options: ReadOptions | None = None, fmt: str | None = None, profile: bool = True
) -> SchemaSnapshot:
    from autogenerator.ingest import inspect_file
    from autogenerator.plugin_host import PluginRegistry

    return inspect_file(path, PluginRegistry.discover(), options, fmt, profile=profile)


def coverage_report(manifest: HistoryManifest) -> CoverageReport:
    from autogenerator.history import coverage_report as _report

    return _report(manifest)


def default_period(manifest: HistoryManifest) -> Period:
    from autogenerator.history import default_report_period

    return default_report_period(manifest)


def history_frame(manifest: HistoryManifest, columns: list[str] | None = None) -> pl.LazyFrame:
    """Действующая история источника — ленивая таблица для своего кода и Jupyter."""
    from autogenerator.history import history_view

    return history_view(manifest, columns)


def export_history(manifest: HistoryManifest, out: str | Path, columns: list[str] | None = None) -> int:
    """Действующая история в один Parquet; возвращает число строк."""
    import polars as pl

    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    history_frame(manifest, columns).sink_parquet(target)
    return int(pl.scan_parquet(target).select(pl.len()).collect().item())


def rows_outside(data_uri: str, source: SourceSpec, period: Period) -> int:
    from autogenerator.history import rows_outside as _outside

    pc = source.period_column
    return _outside(data_uri, pc, source.column(pc).dtype, period)


def plugin_manifest(isolated: bool = True) -> PluginManifest:
    """Манифест плагинов; по умолчанию — из отдельного процесса, как его получает сервер."""
    from autogenerator.plugin_host import PluginRegistry, discover_manifest_isolated

    return discover_manifest_isolated() if isolated else PluginRegistry.discover().manifest()


def load_scenario(data: Any, where: str = "сценарий") -> ScenarioSpec:
    from autogenerator.engine import load_scenario as _load

    return _load(data, where)
