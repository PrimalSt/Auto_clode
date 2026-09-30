"""Задание ``run``: единственное место, где модули обработки собираются вместе
(ARCHITECTURE.md, раздел 4.2, правило 1).

Порядок: разбор сценария (engine) → импорт шаблона (theme) и проверка слайдов (render) →
для каждого файла: снимок структуры (ingest), сверка (schema), запись загрузки (ingest),
период загрузки (history) → отчётный период → расчёты (engine) → сборка (render).

Модули не знают друг о друге: они обмениваются моделями из ``contracts``, а связывает их
этот файл. Модули и плагины импортируются лениво, внутри функций, — как в исполнителе,
которому для каждого типа задания нужны только свои модули.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autogenerator.contracts import (
    AgenError,
    DateSpan,
    DType,
    ErrorCode,
    HistoryManifest,
    Issue,
    IssueLevel,
    Period,
    RunRequest,
    RunResult,
    ScenarioSpec,
    SchemaSnapshot,
    SourceSpec,
    UploadRef,
    UploadStatus,
)

if TYPE_CHECKING:
    import polars as pl

    from autogenerator.engine import InputSchema
    from autogenerator.plugin_host import PluginRegistry


class ManifestHistory:
    """``HistoryProvider`` для движка: история входов по манифестам через модуль ``history``."""

    def __init__(self, manifests: dict[str, HistoryManifest]):
        self.manifests = manifests

    def period_column(self, input_id: str) -> str:
        return self.manifests[input_id].period_column

    def columns(self, input_id: str) -> dict[str, DType]:
        return self.manifests[input_id].columns

    def scan(
        self,
        input_id: str,
        columns: list[str] | None = None,
        lower: date | None = None,
        upper_exclusive: date | None = None,
    ) -> pl.LazyFrame:
        from autogenerator.history import history_view

        return history_view(self.manifests[input_id], columns, lower, upper_exclusive)

    def coverage(self, input_id: str) -> list[DateSpan]:
        from autogenerator.history import coverage

        return coverage(self.manifests[input_id])


def _schemas(scenario: ScenarioSpec, sources: dict[str, SourceSpec]) -> dict[str, InputSchema]:
    from autogenerator.engine import InputSchema

    return {
        i.id: InputSchema(sources[i.source].period_column, sources[i.source].dtypes)
        for i in scenario.inputs
        if i.source in sources
    }


def _source_issues(scenario: ScenarioSpec, sources: dict[str, SourceSpec]) -> list[Issue]:
    return [
        Issue(
            level=IssueLevel.ERROR,
            node=f"input:{i.id}",
            message=f"источник «{i.source}» не описан (есть: {', '.join(sorted(sources)) or 'нет'})",
        )
        for i in scenario.inputs
        if i.source not in sources
    ]


def validate(req: RunRequest, registry: PluginRegistry | None = None) -> list[Issue]:
    """Проверить сценарий без данных: источники, плагины, параметры, ссылки, макеты шаблона."""
    from autogenerator.engine import analyze
    from autogenerator.plugin_host import PluginRegistry
    from autogenerator.render import validate_slides
    from autogenerator.theme import import_template

    registry = registry or PluginRegistry.discover()
    sources = {s.id: s for s in req.sources}
    issues = _source_issues(req.scenario, sources)
    plan = analyze(req.scenario, registry, _schemas(req.scenario, sources))
    issues += plan.issues
    with tempfile.TemporaryDirectory(prefix="agen-validate-") as tmp:
        theme = import_template(req.theme, tmp)
    issues += validate_slides(req.scenario, registry, theme)
    return issues


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, "model_dump_json"):
        path.write_text(data.model_dump_json(indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _ingest_input(
    input_id: str,
    source: SourceSpec,
    files: list[str],
    required: set[str],
    registry: PluginRegistry,
    workdir: Path,
    accept_cast_errors: bool,
    issues: list[Issue],
) -> tuple[HistoryManifest, list[dict[str, Any]]]:
    from autogenerator.history import upload_period
    from autogenerator.ingest import inspect_file, write_upload
    from autogenerator.schema import reconcile

    node = f"input:{input_id}"
    uploads: list[UploadRef] = []
    log: list[dict[str, Any]] = []
    for seq, f in enumerate(files, start=1):
        path = Path(f)
        snap: SchemaSnapshot = inspect_file(path, registry, source.options, fmt=source.format)
        rec = reconcile(source, snap, required)
        if rec.status == "blocked":
            raise AgenError(
                ErrorCode.SCHEMA_BLOCKED,
                f"Файл {path.name} не подходит к источнику «{source.name}»:\n  " + "\n  ".join(rec.messages),
                details=rec.model_dump(),
            )
        for msg in rec.messages:
            issues.append(Issue(level=IssueLevel.INFO, node=node, message=f"{path.name}: {msg}"))
        upload_id = f"{input_id}-{seq:03d}"
        res = write_upload(
            path,
            registry,
            source=source,
            mapping=rec.mapping,
            upload_id=upload_id,
            upload_seq=seq,
            out_dir=workdir / "uploads" / input_id / f"{seq:03d}",
            options=snap.options,
            required=required,
        )
        _dump(workdir / "uploads" / input_id / f"{seq:03d}.json", res)
        if res.status == UploadStatus.NEEDS_REVIEW:
            text = f"{path.name}: " + "; ".join(res.review_reasons)
            if not accept_cast_errors:
                raise AgenError(
                    ErrorCode.CAST_REVIEW,
                    f"Загрузка требует проверки — {text}",
                    hint="Исправьте файл или запустите с --accept-cast-errors, чтобы принять такие строки "
                    "(нераспознанные значения станут пустыми).",
                )
            issues.append(Issue(level=IssueLevel.WARNING, node=node, message=f"принято с ошибками: {text}"))
        else:
            for ci in res.cast_issues:
                issues.append(
                    Issue(
                        level=IssueLevel.WARNING,
                        node=node,
                        message=f"{path.name}: в столбце «{ci.column}» не распознано {ci.errors} значений "
                        f"(например, {', '.join(ci.examples[:3])}); они пустые",
                    )
                )
        if res.null_period_rows:
            issues.append(
                Issue(
                    level=IssueLevel.WARNING,
                    node=node,
                    message=f"{path.name}: {res.null_period_rows} строк без даты в «{source.period_column}»; "
                    "они не попадут ни в одно окно",
                )
            )
        if res.period_min is None or res.period_max is None:
            raise AgenError(
                ErrorCode.HISTORY_EMPTY,
                f"В файле {path.name} нет ни одной даты в «{source.period_column}»",
            )
        period = upload_period(res.period_min, res.period_max, source.period_type)
        uploads.append(
            UploadRef(
                id=upload_id,
                seq=seq,
                uri=res.data_uri,
                period=period,
                rows=res.rows,
                original_name=path.name,
            )
        )
        log.append({"file": str(path), "upload": upload_id, "rows": res.rows, "period": period.key})
    manifest = HistoryManifest(
        source_id=source.id,
        source_version=source.version,
        period_column=source.period_column,
        period_type=source.period_type,
        overlap_policy=source.overlap_policy,
        keys=source.keys,
        columns=source.dtypes,
        uploads=uploads,
    )
    _dump(workdir / "manifests" / f"{input_id}.json", manifest)
    return manifest, log


def output_path(req: RunRequest, period: Period) -> Path:
    from autogenerator.render import sanitize_filename

    if req.output:
        return Path(req.output)
    name = sanitize_filename(req.scenario.output_name.replace("{period}", period.key)) + ".pptx"
    return Path(req.output_dir or ".") / name


def run(req: RunRequest) -> RunResult:
    """Выполнить задание ``run``. Ошибки данных и узлов — в ``RunResult.issues``; ошибки,
    из-за которых отчёт не собрать, — там же с уровнем ``error`` и ``ok=False``."""
    from autogenerator.plugin_host import PluginRegistry

    t0 = time.perf_counter()
    temp = req.workdir is None
    workdir = Path(req.workdir) if req.workdir else Path(tempfile.mkdtemp(prefix="agen-run-"))
    workdir.mkdir(parents=True, exist_ok=True)
    result = RunResult(ok=False, scenario=req.scenario.name, workdir=None if temp else str(workdir))
    try:
        _run(req, workdir, result, PluginRegistry.discover())
    except AgenError as e:
        result.issues.append(Issue(level=IssueLevel.ERROR, code=str(e.code), message=str(e)))
    finally:
        result.seconds = round(time.perf_counter() - t0, 2)
        if temp:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            _dump(workdir / "run.json", result)
    return result


def _run(req: RunRequest, workdir: Path, result: RunResult, registry: PluginRegistry) -> None:
    from autogenerator.engine import analyze, execute
    from autogenerator.history import default_report_period
    from autogenerator.render import build_presentation, validate_slides
    from autogenerator.theme import import_template

    scenario = req.scenario
    sources = {s.id: s for s in req.sources}
    result.issues += _source_issues(scenario, sources)
    plan = analyze(scenario, registry, _schemas(scenario, sources))
    result.issues += plan.issues
    theme = import_template(req.theme, workdir / "theme")
    _dump(workdir / "theme" / "manifest.json", theme)
    result.issues += validate_slides(scenario, registry, theme)
    if result.errors:
        return

    manifests: dict[str, HistoryManifest] = {}
    for inp in scenario.inputs:
        files = req.inputs.get(inp.id) or []
        if not files:
            raise AgenError(
                ErrorCode.HISTORY_EMPTY,
                f"Для входа «{inp.id}» не передано ни одного файла выгрузки",
            )
        required = set(plan.usage.get(inp.id, {}))
        manifests[inp.id], result.inputs[inp.id] = _ingest_input(
            inp.id,
            sources[inp.source],
            files,
            required,
            registry,
            workdir,
            req.accept_cast_errors,
            result.issues,
        )

    period = req.period or default_report_period(manifests[scenario.main_input.id])
    result.period = period
    engine_result = execute(plan, registry, ManifestHistory(manifests), period, workdir / "engine")
    result.nodes += engine_result.nodes
    result.issues += engine_result.issues
    _save_engine_outputs(workdir / "engine" / "outputs", engine_result)

    rendered = build_presentation(scenario, theme, engine_result, registry, output_path(req, period))
    result.nodes += rendered.nodes
    result.issues += rendered.issues
    result.slides = rendered.slides
    result.output_path = rendered.output_path
    result.ok = rendered.output_path is not None and not result.errors


def _save_engine_outputs(folder: Path, engine_result: Any) -> None:
    """Наборы и показатели — на диск, чтобы ``python -m autogenerator.render`` мог собрать
    отчёт из них без расчётов."""
    import pyarrow.parquet as pq

    folder.mkdir(parents=True, exist_ok=True)
    for k, t in engine_result.datasets.items():
        pq.write_table(t, folder / f"{k}.parquet")
    _dump(folder / "metrics.json", engine_result.metrics)
