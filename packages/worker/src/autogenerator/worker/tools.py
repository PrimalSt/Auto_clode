"""Небольшие задания для CLI и интерфейса: снимок структуры файла, манифест плагинов,
покрытие и действующая история источника."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from autogenerator.contracts import (
    CoverageReport,
    EnvironmentInfo,
    HistoryManifest,
    Period,
    PeriodFrom,
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

    if source.period_from == PeriodFrom.UPLOAD:
        return 0  # все строки — в периоде загрузки по определению
    pc = source.period_column
    return _outside(data_uri, pc, source.column(pc).dtype, period)


def plugin_manifest(isolated: bool = True) -> PluginManifest:
    """Манифест плагинов; по умолчанию — из отдельного процесса, как его получает сервер."""
    from autogenerator.plugin_host import PluginRegistry, discover_manifest_isolated

    return discover_manifest_isolated() if isolated else PluginRegistry.discover().manifest()


def load_scenario(data: Any, where: str = "сценарий") -> ScenarioSpec:
    from autogenerator.engine import load_scenario as _load

    return _load(data, where)


def column_usage(scenario: ScenarioSpec, sources: list[SourceSpec]) -> dict[str, dict[str, list[str]]]:
    """Вход → столбец источника → узлы, которые его явно используют (шаги, наборы,
    показатели): по ним сверка структуры решает, какие столбцы обязательны (F-603, F-606)."""
    from autogenerator.engine import InputSchema, analyze
    from autogenerator.plugin_host import PluginRegistry

    by_id = {s.id: s for s in sources}
    schemas = {i.id: InputSchema.from_source(by_id[i.source]) for i in scenario.inputs if i.source in by_id}
    return analyze(scenario, PluginRegistry.discover(), schemas).usage


def suggest_id(name: str, taken: set[str] | None = None) -> str:
    """Латинский id по названию («Продажи по регионам» → ``prodazhi_po_regionam``)."""
    from autogenerator.schema import suggest_id as _suggest

    return _suggest(name, taken)


LIBRARIES = ("polars", "duckdb", "pyarrow", "pandas", "python-pptx", "lxml", "sqlglot", "jinja2", "fastexcel")


def environment_info(manifest: PluginManifest | None = None) -> EnvironmentInfo:
    """Версии приложения, библиотек и плагинов для журнала запуска (F-608)."""
    import hashlib
    import platform
    from importlib.metadata import PackageNotFoundError, distributions, version

    def ver(name: str) -> str | None:
        try:
            return version(name)
        except PackageNotFoundError:
            return None

    pkgs = sorted({f"{d.metadata['Name']}=={d.version}".lower() for d in distributions() if d.metadata["Name"]})
    # плагины — по пакетам: в одном пакете их обычно несколько, версия у них общая
    plugins = {p.distribution or p.name: p.version or "?" for p in (manifest.plugins if manifest else [])}
    return EnvironmentInfo(
        app_version=ver("autogenerator-worker") or "dev",
        python=platform.python_version(),
        libraries={n: v for n in LIBRARIES if (v := ver(n))},
        plugins=plugins,
        env_hash=hashlib.sha256("\n".join(pkgs).encode()).hexdigest(),
    )
