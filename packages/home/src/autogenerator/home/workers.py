"""Что папке данных нужно от исполнителя.

Тяжёлую работу (чтение файла, проверку сценария, сборку отчёта) ``Home`` не делает сам, а
вызывает функции исполнителя. В своём коде, Jupyter и CLI это модуль ``autogenerator.worker``
(вызовы в том же процессе: ``autogenerator.api.Home``), в сервере приложения — исполнители в
отдельных процессах. Поэтому этот пакет не импортирует модули обработки.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from autogenerator.contracts import (
    CoverageReport,
    HistoryManifest,
    IngestRequest,
    IngestResult,
    Issue,
    Period,
    PeriodFrom,
    PeriodUnit,
    PluginManifest,
    PreviewRequest,
    PreviewResult,
    ProgressCallback,
    ReadOptions,
    RunRequest,
    RunResult,
    ScenarioSpec,
    SchemaSnapshot,
    SourceSpec,
    ThemeManifest,
)


class WorkerApi(Protocol):
    """Функции исполнителя (``autogenerator.worker``), которые вызывает ``Home``."""

    def draft_source(
        self,
        path: str | Path,
        source_id: str,
        name: str | None = None,
        period_column: str | None = None,
        period_type: PeriodUnit | None = None,
        options: ReadOptions | None = None,
        fmt: str | None = None,
        *,
        period_from: PeriodFrom | None = None,
    ) -> tuple[SourceSpec, SchemaSnapshot]: ...

    def inspect(
        self, path: str | Path, options: ReadOptions | None = None, fmt: str | None = None, profile: bool = True
    ) -> SchemaSnapshot: ...

    def ingest_upload(
        self,
        req: IngestRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> IngestResult: ...

    def with_aliases(self, source: SourceSpec, pairs: dict[str, str]) -> SourceSpec: ...

    def normalize_name(self, name: str) -> str: ...

    def column_usage(self, scenario: ScenarioSpec, sources: list[SourceSpec]) -> dict[str, dict[str, list[str]]]: ...

    def rows_outside(self, data_uri: str, source: SourceSpec, period: Period) -> int: ...

    def coverage_report(self, manifest: HistoryManifest) -> CoverageReport: ...

    def default_period(self, manifest: HistoryManifest) -> Period: ...

    def export_history(self, manifest: HistoryManifest, out: str | Path, columns: list[str] | None = None) -> int: ...

    def suggest_id(self, name: str, taken: set[str] | None = None) -> str: ...

    def load_scenario(self, data: Any, where: str = "сценарий") -> ScenarioSpec: ...

    def validate(self, req: RunRequest) -> list[Issue]: ...

    def import_theme(
        self,
        path: str | Path,
        out_dir: str | Path | None = None,
        roles: dict[str, str] | None = None,
        strict_roles: bool = False,
    ) -> ThemeManifest: ...

    def run(self, req: RunRequest) -> RunResult: ...

    def preview(self, req: PreviewRequest) -> PreviewResult: ...

    def plugin_manifest(self, isolated: bool = True) -> PluginManifest: ...
