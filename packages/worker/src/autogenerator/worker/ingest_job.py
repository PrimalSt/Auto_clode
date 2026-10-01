"""Задания ``ingest`` и ``draft_source``: загрузка файла в историю источника и черновик
источника по первой выгрузке (ARCHITECTURE.md, разделы 6.1–6.3, 6.6).

Порядок загрузки: шапка или выборка файла (ingest) → сверка с источником (schema) → запись
Parquet по месяцам (ingest) → период загрузки, строки вне периода и пересечения с прежними
загрузками (history) → точный профиль (ingest). Метаданные задание не пишет: результат
фиксирует тот, кто его поставил.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from autogenerator.contracts import (
    AgenError,
    DType,
    ErrorCode,
    IngestRequest,
    IngestResult,
    Issue,
    IssueLevel,
    OverlapPolicy,
    PeriodUnit,
    ProgressCallback,
    ReadOptions,
    ReconcileStatus,
    SchemaSnapshot,
    SourceSpec,
    UploadStatus,
)

if TYPE_CHECKING:
    from autogenerator.plugin_host import PluginRegistry


def _registry(registry: PluginRegistry | None) -> PluginRegistry:
    from autogenerator.plugin_host import PluginRegistry

    return registry or PluginRegistry.discover()


def ingest_upload(
    req: IngestRequest,
    registry: PluginRegistry | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> IngestResult:
    """Прочитать файл и записать загрузку источника в ``req.out_dir``."""
    from autogenerator.history import overlapping_uploads, rows_outside, upload_period
    from autogenerator.ingest import choose_reader, header_snapshot, inspect_file, profile_upload, write_upload
    from autogenerator.schema import reconcile

    registry = _registry(registry)
    source = req.source
    path = Path(req.path)
    node = f"source:{source.id}"
    options = (req.options or ReadOptions()).merged(source.options)
    reader = choose_reader(path, registry, source.format)
    if reader.sample_reads_all:
        snap = header_snapshot(path, registry, options, source.format)
    else:
        snap = inspect_file(path, registry, options, source.format, profile=False)
    rec = reconcile(source, snap, set(req.required))
    if rec.status == ReconcileStatus.BLOCKED:
        raise AgenError(
            ErrorCode.SCHEMA_BLOCKED,
            f"Файл {path.name} не подходит к источнику «{source.name}»:\n  " + "\n  ".join(rec.messages),
            details=rec.model_dump(),
        )
    issues = [Issue(level=IssueLevel.INFO, node=node, message=f"{path.name}: {m}") for m in rec.messages]

    res = write_upload(
        path,
        registry,
        source=source,
        mapping=rec.mapping,
        upload_id=req.upload_id,
        upload_seq=req.upload_seq,
        out_dir=req.out_dir,
        options=snap.options,
        required=set(req.required),
        progress=progress,
        cancelled=cancelled,
    )
    try:
        if res.period_min is None or res.period_max is None:
            raise AgenError(
                ErrorCode.HISTORY_EMPTY,
                f"В файле {path.name} нет ни одной даты в столбце периода «{source.column(source.period_column).name}»",
                hint="Проверьте, тот ли столбец выбран периодом, и формат дат в нём.",
            )
        from_data = upload_period(res.period_min, res.period_max, source.period_type)
        period = req.period or from_data
        pc = source.period_column
        outside = 0 if period == from_data else rows_outside(res.data_uri, pc, source.column(pc).dtype, period)
        overlaps = overlapping_uploads(req.history, period) if req.history else []
        profile = {}
        if req.profile:
            if progress is not None:
                from autogenerator.contracts import ReadProgress

                progress(ReadProgress("профиль столбцов", 0, None, "rows"))
            profile = profile_upload(res.data_uri, [c.id for c in source.columns])
    except BaseException:
        shutil.rmtree(res.data_uri, ignore_errors=True)
        raise

    # Снимок загрузки: столбцы файла с типами источника и точным профилем.
    by_name = rec.mapping
    for c in snap.columns:
        cid = by_name.get(c.source_name)
        if cid is not None:
            if reader.sample_reads_all:
                c.dtype = source.column(cid).dtype
            c.profile = profile.get(cid)

    for ci in res.cast_issues:
        level = IssueLevel.ERROR if res.status == UploadStatus.NEEDS_REVIEW else IssueLevel.WARNING
        issues.append(
            Issue(
                level=level,
                node=node,
                message=f"{path.name}: в столбце «{ci.column}» не распознано {ci.errors} значений "
                f"(например, {', '.join(ci.examples[:3])}); они пустые, исходные строки — в rejects.parquet",
            )
        )
    if res.null_period_rows:
        issues.append(
            Issue(
                level=IssueLevel.WARNING,
                node=node,
                message=f"{path.name}: {res.null_period_rows} строк без даты в «{pc}»; они не попадут ни в одно окно",
            )
        )
    if outside:
        issues.append(
            Issue(
                level=IssueLevel.WARNING,
                node=node,
                message=f"{path.name}: {outside} строк с датой вне периода загрузки {period.key}; "
                "при замене периода они не заменяются",
            )
        )
    policy = req.overlap_policy or (None if source.overlap_policy == OverlapPolicy.ASK else source.overlap_policy)
    needs_choice = bool(overlaps) and source.overlap_policy == OverlapPolicy.ASK and req.overlap_policy is None
    if overlaps and policy == OverlapPolicy.APPEND:
        issues.append(
            Issue(
                level=IssueLevel.WARNING,
                node=node,
                message=f"{path.name}: период {period.key} уже загружен ("
                + ", ".join(f"#{u.seq} {u.period.key}" for u in overlaps)
                + "); строки добавятся к ним — проверьте, нет ли двойного учёта",
            )
        )
    return IngestResult(
        snapshot=snap,
        reconcile=rec,
        upload=res,
        period=period,
        period_from_data=from_data,
        rows_outside_period=outside,
        overlaps=[u.id for u in overlaps],
        overlap_policy=req.overlap_policy,
        needs_overlap_choice=needs_choice,
        profile=profile,
        issues=issues,
    )


def draft_source(
    path: str | Path,
    source_id: str,
    name: str | None = None,
    period_column: str | None = None,
    period_type: PeriodUnit | None = None,
    options: ReadOptions | None = None,
    fmt: str | None = None,
    registry: PluginRegistry | None = None,
) -> tuple[SourceSpec, SchemaSnapshot]:
    """Черновик источника по выгрузке: столбцы с id и типами, столбец и тип периода."""
    from datetime import date

    from autogenerator.history import guess_period_type
    from autogenerator.ingest import inspect_file
    from autogenerator.schema import draft_source as _draft

    p = Path(path)
    snap = inspect_file(p, _registry(registry), options, fmt)
    spec = _draft(snap, source_id, name or p.stem, period_column, PeriodUnit.MONTH, options)
    if period_type is None:
        pcol = next(c for c in snap.columns if c.source_name == spec.column(spec.period_column).name)
        prof = pcol.profile
        period_type = PeriodUnit.MONTH
        if prof is not None and prof.min and prof.max:
            with suppress(ValueError):
                period_type = guess_period_type(date.fromisoformat(prof.min[:10]), date.fromisoformat(prof.max[:10]))
    spec = spec.model_copy(update={"period_type": period_type})
    if spec.column(spec.period_column).dtype not in (DType.DATE, DType.DATETIME):
        raise AgenError(ErrorCode.SPEC_INVALID, "Столбец периода должен быть датой")
    return spec, snap
