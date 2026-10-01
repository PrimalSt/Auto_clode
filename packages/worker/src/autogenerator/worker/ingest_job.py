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
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from autogenerator.contracts import (
    AgenError,
    ColumnSnapshot,
    DType,
    ErrorCode,
    IngestRequest,
    IngestResult,
    Issue,
    IssueLevel,
    OverlapPolicy,
    Period,
    PeriodFrom,
    PeriodUnit,
    ProgressCallback,
    ReaderPlugin,
    ReadOptions,
    ReconcileResult,
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
    """Прочитать файл (или несколько файлов одной выгрузки) и записать загрузку источника
    в ``req.out_dir``."""
    from autogenerator.history import overlapping_uploads, period_from_name, rows_outside, upload_period
    from autogenerator.ingest import FilePart, profile_upload, write_upload

    registry = _registry(registry)
    source = req.source
    paths = [Path(f) for f in req.files]
    path = paths[0]
    label = path.name if len(paths) == 1 else " + ".join(x.name for x in paths)
    node = f"source:{source.id}"
    options = (req.options or ReadOptions()).merged(source.options)

    # Сверка каждого файла: части одной выгрузки могут отличаться порядком столбцов.
    issues: list[Issue] = []
    checked: list[tuple[Path, SchemaSnapshot, ReconcileResult]] = []
    for f in paths:
        snap, rec = _check_file(f, source, options, set(req.required), registry)
        name = f.name
        if rec.status == ReconcileStatus.BLOCKED:
            raise AgenError(
                ErrorCode.SCHEMA_BLOCKED,
                f"Файл {name} не подходит к источнику «{source.name}»:\n  " + "\n  ".join(rec.warnings + rec.messages),
                details=rec.model_dump(),
            )
        issues += [Issue(level=IssueLevel.WARNING, node=node, message=f"{name}: {m}") for m in rec.warnings]
        issues += [Issue(level=IssueLevel.INFO, node=node, message=f"{name}: {m}") for m in rec.messages]
        checked.append((f, snap, rec))
    _, snap, rec = checked[0]

    by_upload = source.period_from == PeriodFrom.UPLOAD
    fixed = None
    if by_upload:
        fixed = req.period or period_from_name(path.name, source.period_type)
        if fixed is None:
            raise AgenError(
                ErrorCode.PERIOD_INVALID,
                f"Не понятно, за какой период выгрузка {label}: у источника «{source.name}» период задаётся "
                "при загрузке, а в имени файла нет месяца",
                hint="Укажите период: --period 2026-01 (или добавьте месяц в имя файла: «…_2026-01.xlsx»).",
            )

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
        more=[FilePart(f, r.mapping, sn.options) for f, sn, r in checked[1:]],
        fixed_period=fixed.start if fixed is not None else None,
    )
    pc = source.period_column
    try:
        if res.period_min is None or res.period_max is None:
            raise AgenError(
                ErrorCode.HISTORY_EMPTY,
                f"В файле {label} нет ни одной даты в столбце периода «{source.column(pc).name}»",
                hint="Проверьте, тот ли столбец выбран периодом, и формат дат в нём.",
            )
        if fixed is not None:
            from_data = period = fixed
            outside = 0
        else:
            from_data = upload_period(res.period_min, res.period_max, source.period_type)
            period = req.period or from_data
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

    # Снимок загрузки: столбцы первого файла с типами источника и точным профилем.
    reader = _reader_for(path, registry, source.format)
    by_name = rec.mapping
    for c in snap.columns:
        cid = by_name.get(c.source_name)
        if cid is not None:
            if reader.sample_reads_all:
                c.dtype = source.column(cid).dtype
            c.profile = profile.get(cid)

    rejects = Path(res.rejects_uri).name if res.rejects_uri else "rejects.parquet"
    for ci in res.cast_issues:
        level = IssueLevel.ERROR if res.status == UploadStatus.NEEDS_REVIEW else IssueLevel.WARNING
        issues.append(
            Issue(
                level=level,
                node=node,
                message=f"{label}: в столбце «{ci.column}» не распознано {ci.errors} значений "
                f"(например, {', '.join(ci.examples[:3])}); они пустые, исходные строки — в {rejects}",
            )
        )
    if res.null_period_rows:
        issues.append(
            Issue(
                level=IssueLevel.WARNING,
                node=node,
                message=f"{label}: {res.null_period_rows} строк без даты в «{pc}»; они не попадут ни в одно окно",
            )
        )
    if outside:
        issues.append(
            Issue(
                level=IssueLevel.WARNING,
                node=node,
                message=f"{label}: {outside} строк с датой вне периода загрузки {period.key}; "
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
                message=f"{label}: период {period.key} уже загружен ("
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


def _reader_for(path: Path, registry: PluginRegistry, fmt: str | None) -> ReaderPlugin:
    from autogenerator.ingest import choose_reader

    return choose_reader(path, registry, fmt)


def _check_file(
    path: Path, source: SourceSpec, options: ReadOptions, required: set[str], registry: PluginRegistry
) -> tuple[SchemaSnapshot, ReconcileResult]:
    """Снимок структуры файла и сверка с источником. Для Excel снимок — по шапке: выборка
    прочитала бы лист целиком, а типы всё равно берутся из источника."""
    from autogenerator.ingest import header_snapshot, inspect_file
    from autogenerator.schema import reconcile

    reader = _reader_for(path, registry, source.format)
    if reader.sample_reads_all:
        snap = header_snapshot(path, registry, options, source.format)
    else:
        snap = inspect_file(path, registry, options, source.format, profile=False)
    return snap, reconcile(source, snap, required)


def draft_source(
    path: str | Path,
    source_id: str,
    name: str | None = None,
    period_column: str | None = None,
    period_type: PeriodUnit | None = None,
    options: ReadOptions | None = None,
    fmt: str | None = None,
    registry: PluginRegistry | None = None,
    period_from: PeriodFrom | None = None,
) -> tuple[SourceSpec, SchemaSnapshot]:
    """Черновик источника по выгрузке: столбцы с id и типами, столбец и тип периода.

    Столбец периода, если он не указан, — столбец дат, которые укладываются в один
    календарный период (месяц, квартал, год): так выгрузка за месяц и выглядит. Если такого
    столбца нет, а в имени файла есть месяц, выгрузка считается срезом: период задаётся при
    загрузке (``period_from=upload``).
    """
    from autogenerator.history import guess_period_type, period_from_name
    from autogenerator.ingest import inspect_file
    from autogenerator.schema import draft_source as _draft

    p = Path(path)
    snap = inspect_file(p, _registry(registry), options, fmt)
    if period_from is None:
        period_from = PeriodFrom.COLUMN
        if period_column is None:
            named = period_from_name(p.name, period_type or PeriodUnit.MONTH)
            best = _period_column(snap, named)
            if best is not None:
                period_column = best.source_name
            elif named is not None:
                period_from = PeriodFrom.UPLOAD
    if period_from == PeriodFrom.UPLOAD:
        spec = _draft(snap, source_id, name or p.stem, None, period_type or PeriodUnit.MONTH, options, period_from)
        return spec, snap
    spec = _draft(snap, source_id, name or p.stem, period_column, PeriodUnit.MONTH, options)
    if period_type is None:
        pcol = next(c for c in snap.columns if c.source_name == spec.column(spec.period_column).name)
        span = _span(pcol)
        period_type = guess_period_type(*span) if span else PeriodUnit.MONTH
    spec = spec.model_copy(update={"period_type": period_type})
    if spec.column(spec.period_column).dtype not in (DType.DATE, DType.DATETIME):
        raise AgenError(ErrorCode.SPEC_INVALID, "Столбец периода должен быть датой")
    return spec, snap


def _span(col: ColumnSnapshot) -> tuple[date, date] | None:
    prof = col.profile
    if prof is None or not prof.min or not prof.max:
        return None
    try:
        return date.fromisoformat(prof.min[:10]), date.fromisoformat(prof.max[:10])
    except ValueError:
        return None


def _period_column(snap: SchemaSnapshot, named: Period | None = None) -> ColumnSnapshot | None:
    """Столбец периода по умолчанию: даты укладываются в один месяц (квартал, год) и, если
    в имени файла есть период ``named``, заходят в него; среди таких — где даты не все
    одинаковые (одна дата бывает у «даты выгрузки»), с «дата» или «период» в названии, потом
    самый заполненный и самый левый. ``None``, если такого столбца нет."""
    from autogenerator.history import guess_period_type
    from autogenerator.schema.draft import PERIOD_HINTS
    from autogenerator.schema.reconcile import normalize_name

    best: tuple[tuple[bool, bool, int, int], ColumnSnapshot] | None = None
    for i, c in enumerate(snap.columns):
        if c.dtype not in (DType.DATE, DType.DATETIME) or (span := _span(c)) is None:
            continue
        if guess_period_type(*span) == PeriodUnit.RANGE:
            continue
        if named is not None and (span[1] < named.start or span[0] >= named.end_exclusive):
            continue
        hinted = any(h in normalize_name(c.source_name) for h in PERIOD_HINTS)
        rank = (span[0] != span[1], hinted, c.non_null, -i)
        if best is None or rank > best[0]:
            best = (rank, c)
    return best[1] if best else None
