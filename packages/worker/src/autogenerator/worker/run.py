"""Задание ``run``: единственное место, где модули обработки собираются вместе
(ARCHITECTURE.md, раздел 4.2, правило 1).

Порядок: разбор сценария (engine) → импорт шаблона (theme) и проверка слайдов (render) →
история каждого входа: из папки данных (манифест приходит в задании) или из переданных
файлов (задание ``ingest`` для каждого, во временную историю) → отчётный период →
расчёты (engine) → сборка (render). Задание ``preview`` проходит тот же путь до расчётов и
показывает один узел сценария.

Модули не знают друг о друге: они обмениваются моделями из ``contracts``, а связывает их
этот файл. Модули и плагины импортируются лениво, внутри функций, — как в исполнителе,
которому для каждого типа задания нужны только свои модули.
"""

from __future__ import annotations

import hashlib
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
    IngestRequest,
    Issue,
    IssueLevel,
    Period,
    PreviewRequest,
    PreviewResult,
    RunRequest,
    RunResult,
    ScenarioSpec,
    SourceSpec,
    UploadRef,
    UploadStatus,
)

if TYPE_CHECKING:
    import polars as pl

    from autogenerator.engine import EngineOptions, InputSchema, ScenarioPlan
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

    def fingerprint(self, input_id: str) -> str | None:
        """Отпечаток манифеста: меняется с каждой загрузкой, правкой периода или источника."""
        m = self.manifests.get(input_id)
        if m is None:
            return None
        return hashlib.sha256(m.model_dump_json().encode("utf-8")).hexdigest()


def _schemas(scenario: ScenarioSpec, sources: dict[str, SourceSpec]) -> dict[str, InputSchema]:
    from autogenerator.engine import InputSchema

    return {i.id: InputSchema.from_source(sources[i.source]) for i in scenario.inputs if i.source in sources}


def engine_options(cache_dir: str | None, temp_dir: str | None) -> EngineOptions:
    """Кэш узлов и временная папка движка: из папки данных, если задание пришло оттуда."""
    from autogenerator.engine import EngineOptions, NodeCache

    return EngineOptions(
        cache=NodeCache(Path(cache_dir)) if cache_dir else None,
        temp_dir=Path(temp_dir) if temp_dir else None,
    )


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
    """Файлы входа — во временную историю в рабочей папке, по порядку загрузки."""
    from .ingest_job import ingest_upload

    node = f"input:{input_id}"
    manifest = HistoryManifest.for_source(source)
    log: list[dict[str, Any]] = []
    for seq, f in enumerate(files, start=1):
        path = Path(f)
        upload_id = f"{input_id}-{seq:03d}"
        res = ingest_upload(
            IngestRequest(
                source=source,
                path=str(path),
                upload_id=upload_id,
                upload_seq=seq,
                out_dir=str(workdir / "uploads" / input_id / f"{seq:03d}"),
                required=sorted(required),
                history=manifest,
                profile=False,
            ),
            registry,
        )
        _dump(workdir / "uploads" / input_id / f"{seq:03d}.json", res)
        issues += [i.model_copy(update={"node": node}) for i in res.issues if i.level != IssueLevel.ERROR]
        up = res.upload
        if up.status == UploadStatus.NEEDS_REVIEW:
            text = f"{path.name}: " + "; ".join(up.review_reasons)
            if not accept_cast_errors:
                raise AgenError(
                    ErrorCode.CAST_REVIEW,
                    f"Загрузка требует проверки — {text}",
                    hint="Исправьте файл или запустите с --accept-cast-errors, чтобы принять такие строки "
                    "(нераспознанные значения станут пустыми).",
                )
            issues.append(Issue(level=IssueLevel.WARNING, node=node, message=f"принято с ошибками: {text}"))
        if res.needs_overlap_choice:
            raise AgenError(
                ErrorCode.OVERLAP_CHOICE,
                f"{path.name}: период {res.period.key} пересекается с более ранними файлами входа «{input_id}», "
                "а у источника правило пересечения «ask»",
                hint="Для запуска из файлов задайте источнику правило пересечения (overlap_policy) явно.",
            )
        manifest.uploads.append(
            UploadRef(
                id=upload_id,
                seq=seq,
                uri=up.data_uri,
                period=res.period,
                rows=up.rows,
                original_name=path.name,
            )
        )
        log.append({"file": str(path), "upload": upload_id, "rows": up.rows, "period": res.period.key})
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
    result.issues += validate_slides(scenario, registry, theme, preview=req.preview)
    if result.errors:
        return

    manifests = _histories(req, plan, registry, workdir, result.issues, result.inputs, result.from_home)

    period = req.period or default_report_period(manifests[scenario.main_input.id])
    result.period = period
    options = engine_options(req.cache_dir, req.temp_dir)
    engine_result = execute(plan, registry, ManifestHistory(manifests), period, workdir / "engine", options)
    result.nodes += engine_result.nodes
    result.issues += engine_result.issues
    _save_engine_outputs(workdir / "engine" / "outputs", engine_result)

    rendered = build_presentation(
        scenario, theme, engine_result, registry, output_path(req, period), preview=req.preview, only=req.slide
    )
    result.nodes += rendered.nodes
    result.issues += rendered.issues
    result.slides = rendered.slides
    result.output_path = rendered.output_path
    result.ok = rendered.output_path is not None and not result.errors
    if req.image and rendered.output_path:
        from .theme_jobs import slide_image

        try:
            result.image_note = slide_image(rendered.output_path, req.image)
            result.image_path = req.image
        except AgenError as e:
            result.issues.append(Issue(level=IssueLevel.WARNING, code=str(e.code), message=str(e)))


def _histories(
    req: RunRequest | PreviewRequest,
    plan: ScenarioPlan,
    registry: PluginRegistry,
    workdir: Path,
    issues: list[Issue],
    log: dict[str, Any],
    from_home: list[str],
    only: set[str] | None = None,
) -> dict[str, HistoryManifest]:
    """История каждого входа (``only`` — только этих): из папки данных или из файлов."""
    scenario = req.scenario
    sources = {s.id: s for s in req.sources}
    manifests: dict[str, HistoryManifest] = {}
    for inp in scenario.inputs:
        if only is not None and inp.id not in only:
            continue
        if inp.id in req.histories:
            # История из папки данных: файлы уже загружены, читаются только нужные месяцы.
            m = req.histories[inp.id]
            manifests[inp.id] = m
            from_home.append(inp.id)
            _dump(workdir / "manifests" / f"{inp.id}.json", m)
            log[inp.id] = [
                {"file": u.original_name, "upload": u.id, "rows": u.rows, "period": u.period.key}
                for u in m.active_uploads
            ]
            continue
        files = req.inputs.get(inp.id) or []
        if not files:
            raise AgenError(
                ErrorCode.HISTORY_EMPTY,
                f"Для входа «{inp.id}» не передано ни одного файла выгрузки",
            )
        required = set(plan.usage.get(inp.id, {}))
        manifests[inp.id], log[inp.id] = _ingest_input(
            inp.id,
            sources[inp.source],
            files,
            required,
            registry,
            workdir,
            req.accept_cast_errors,
            issues,
        )
    return manifests


def preview(req: PreviewRequest) -> PreviewResult:
    """Выполнить задание ``preview``: первые строки и числа строк узла, без сборки .pptx."""
    from autogenerator.engine import analyze, resolve_target
    from autogenerator.engine import preview as engine_preview
    from autogenerator.history import default_report_period
    from autogenerator.plugin_host import PluginRegistry

    t0 = time.perf_counter()
    temp = req.workdir is None
    workdir = Path(req.workdir) if req.workdir else Path(tempfile.mkdtemp(prefix="agen-preview-"))
    workdir.mkdir(parents=True, exist_ok=True)
    result = PreviewResult(target=req.target, period=req.period)
    try:
        registry = PluginRegistry.discover()
        sources = {s.id: s for s in req.sources}
        issues = _source_issues(req.scenario, sources)
        if issues:
            result.issues = issues
            return result
        plan = analyze(req.scenario, registry, _schemas(req.scenario, sources))
        target = resolve_target(plan, req.target)
        closure = plan.closure([target.split("/", 1)[0]])
        needed = {n.split(":", 1)[1] for n in closure if n.startswith("input:")}
        main = req.scenario.main_input.id
        if req.period is None:
            needed.add(main if (main in req.histories or req.inputs.get(main)) else next(iter(sorted(needed)), main))
        log: dict[str, Any] = {}
        prep: list[Issue] = []
        manifests = _histories(req, plan, registry, workdir, prep, log, [], only=needed)
        if req.period is not None:
            period = req.period
        else:
            src = main if main in manifests else sorted(manifests)[0]
            period = default_report_period(manifests[src])
        options = engine_options(req.cache_dir, req.temp_dir)
        result = engine_preview(
            plan,
            registry,
            ManifestHistory(manifests),
            period,
            workdir / "engine",
            target,
            rows=req.rows,
            sample=req.sample,
            options=options,
        )
        result.issues = prep + result.issues
    except AgenError as e:
        result.issues.append(Issue(level=IssueLevel.ERROR, code=str(e.code), message=str(e)))
    finally:
        result.seconds = round(time.perf_counter() - t0, 3)
        if temp:
            shutil.rmtree(workdir, ignore_errors=True)
    return result


def _save_engine_outputs(folder: Path, engine_result: Any) -> None:
    """Наборы и показатели — на диск, чтобы ``python -m autogenerator.render`` мог собрать
    отчёт из них без расчётов."""
    import pyarrow.parquet as pq

    folder.mkdir(parents=True, exist_ok=True)
    for k, t in engine_result.datasets.items():
        pq.write_table(t, folder / f"{k}.parquet")
    _dump(folder / "metrics.json", engine_result.metrics)
