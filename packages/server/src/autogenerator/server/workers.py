"""Исполнитель для ``Home`` в сервере: каждая функция ``WorkerApi`` вызывается в процессе-
исполнителе (``runner.ProcessExecutor``), а сам сервер модули обработки не импортирует.

Загрузки, сборка отчёта и импорт шаблона идут в исполнитель ``main`` (по одному, как и задания
очереди ``main``); проверка сценария, превью и мелкие функции — в ``light``, чтобы окно
отвечало, пока собирается отчёт. Вызов из задания очереди можно отменить: отмена задания
останавливает и вызов исполнителя.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from autogenerator.contracts import (
    CoverageReport,
    ExecutorBackend,
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
from autogenerator.runner import current_job


class RemoteWorker:
    """``WorkerApi`` поверх двух исполнителей: ``main`` и ``light``."""

    def __init__(self, main: ExecutorBackend, light: ExecutorBackend):
        self.main = main
        self.light = light

    def _call(self, ex: ExecutorBackend, fn: str, *args: Any, **kwargs: Any) -> Any:
        job = current_job()
        kw = {k: v for k, v in kwargs.items() if v is not None or k not in ("progress", "cancelled")}
        return ex.call(fn, args, kw, cancelled=job.cancelled if job is not None else None)

    # --- загрузки ------------------------------------------------------------------

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
    ) -> tuple[SourceSpec, SchemaSnapshot]:
        return self._call(
            self.main,
            "draft_source",
            path,
            source_id,
            name,
            period_column,
            period_type,
            options,
            fmt,
            period_from=period_from,
        )

    def inspect(
        self, path: str | Path, options: ReadOptions | None = None, fmt: str | None = None, profile: bool = True
    ) -> SchemaSnapshot:
        return self._call(self.main, "inspect", path, options, fmt, profile)

    def ingest_upload(
        self,
        req: IngestRequest,
        *,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> IngestResult:
        return self._call(self.main, "ingest_upload", req, progress=progress, cancelled=cancelled)

    def with_aliases(self, source: SourceSpec, pairs: dict[str, str]) -> SourceSpec:
        return self._call(self.light, "with_aliases", source, pairs)

    def normalize_name(self, name: str) -> str:
        return self._call(self.light, "normalize_name", name)

    def column_usage(self, scenario: ScenarioSpec, sources: list[SourceSpec]) -> dict[str, dict[str, list[str]]]:
        return self._call(self.light, "column_usage", scenario, sources)

    def rows_outside(self, data_uri: str, source: SourceSpec, period: Period) -> int:
        return self._call(self.light, "rows_outside", data_uri, source, period)

    # --- история -------------------------------------------------------------------

    def coverage_report(self, manifest: HistoryManifest) -> CoverageReport:
        return self._call(self.light, "coverage_report", manifest)

    def default_period(self, manifest: HistoryManifest) -> Period:
        return self._call(self.light, "default_period", manifest)

    def export_history(self, manifest: HistoryManifest, out: str | Path, columns: list[str] | None = None) -> int:
        return self._call(self.main, "export_history", manifest, out, columns)

    # --- сценарии, шаблоны, запуски ------------------------------------------------

    def suggest_id(self, name: str, taken: set[str] | None = None) -> str:
        return self._call(self.light, "suggest_id", name, taken)

    def load_scenario(self, data: Any, where: str = "сценарий") -> ScenarioSpec:
        return self._call(self.light, "load_scenario", data, where)

    def validate(self, req: RunRequest) -> list[Issue]:
        return self._call(self.light, "validate", req)

    def import_theme(
        self,
        path: str | Path,
        out_dir: str | Path | None = None,
        roles: dict[str, str] | None = None,
        strict_roles: bool = False,
    ) -> ThemeManifest:
        return self._call(self.main, "import_theme", path, out_dir, roles, strict_roles)

    def run(self, req: RunRequest) -> RunResult:
        # пробная сборка одного слайда — превью: она не ждёт, пока соберётся отчёт
        return self._call(self.light if req.slide is not None else self.main, "run", req)

    def preview(self, req: PreviewRequest) -> PreviewResult:
        return self._call(self.light, "preview", req)

    def plugin_manifest(self, isolated: bool = True) -> PluginManifest:
        # исполнитель — уже отдельный процесс: ещё один для обнаружения плагинов не нужен
        return self._call(self.light, "plugin_manifest", False)
