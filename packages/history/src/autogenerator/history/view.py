"""Периоды загрузок и действующая история (ARCHITECTURE.md, раздел 6.3).

Всё здесь — чистые функции над манифестом истории: модуль не открывает хранилище метаданных
и ничего не пишет. Загрузки неизменяемы; какие строки участвуют в истории, вычисляется при
чтении по правилу пересечения источника.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import polars as pl

from autogenerator.contracts import (
    AgenError,
    DateSpan,
    DType,
    ErrorCode,
    HistoryManifest,
    OverlapPolicy,
    Period,
    PeriodUnit,
    UploadRef,
)
from autogenerator.contracts.periods import unit_shift, unit_start

SERVICE_COLUMNS = ["_upload_id", "_upload_seq", "_row"]

_POLARS_TYPES: dict[DType, pl.DataType] = {
    DType.STRING: pl.String(),
    DType.INT: pl.Int64(),
    DType.FLOAT: pl.Float64(),
    DType.DATE: pl.Date(),
    DType.DATETIME: pl.Datetime("us"),
    DType.BOOL: pl.Boolean(),
}


# --- Период загрузки -----------------------------------------------------------------


def upload_period(first_day: date, last_day: date, period_type: PeriodUnit) -> Period:
    """Объявленный период загрузки по минимальной и максимальной дате.

    Для календарного типа даты расширяются до целых единиц: выгрузка с 9 по 31 января —
    «январь 2026». Если загрузка захватывает несколько единиц, её период — диапазон из целых
    единиц (``unit=range``). Для типа ``range`` период — сами даты; на этапе M4 пользователь
    будет подтверждать его при загрузке.
    """
    if last_day < first_day:
        raise AgenError(ErrorCode.PERIOD_INVALID, "Последняя дата загрузки раньше первой")
    if period_type == PeriodUnit.RANGE:
        return Period.range(first_day, last_day)
    start = unit_start(first_day, period_type)
    end = unit_shift(unit_start(last_day, period_type), period_type, 1)
    if unit_shift(start, period_type, 1) == end:
        return Period(start=start, end_exclusive=end, unit=period_type)
    return Period(start=start, end_exclusive=end, unit=PeriodUnit.RANGE)


def default_report_period(manifest: HistoryManifest) -> Period:
    """Отчётный период по умолчанию: период активной загрузки с самым поздним концом
    (а не последней по порядку), поэтому дозагрузка старых периодов его не сдвигает.
    Если та загрузка захватывает несколько единиц — последняя из них."""
    active = manifest.active_uploads
    if not active:
        raise AgenError(ErrorCode.HISTORY_EMPTY, f"У источника {manifest.source_id} нет загрузок")
    latest = max(active, key=lambda u: (u.period.end_exclusive, u.seq))
    p = latest.period
    if p.unit == PeriodUnit.RANGE and manifest.period_type != PeriodUnit.RANGE:
        return Period.containing(p.last_day, manifest.period_type)
    return p


def coverage(manifest: HistoryManifest) -> list[DateSpan]:
    """Объединённые периоды активных загрузок по возрастанию."""
    spans = sorted((u.period.start, u.period.end_exclusive) for u in manifest.active_uploads)
    merged: list[list[date]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [DateSpan(start=s, end_exclusive=e) for s, e in merged]


# --- Действующая история ----------------------------------------------------------------


def _bound(value: date, dtype: DType) -> pl.Expr:
    if dtype == DType.DATETIME:
        return pl.lit(datetime.combine(value, time()), dtype=pl.Datetime("us"))
    return pl.lit(value, dtype=pl.Date())


def _month_files(up: UploadRef, lower: date | None, upper_exclusive: date | None) -> list[str]:
    """Файлы загрузки, пропуская месяцы вне границ: раскладка по месяцам для этого и нужна."""
    root = Path(up.uri)
    files: list[str] = []
    lo = lower.strftime("%Y-%m") if lower else None
    hi = upper_exclusive.strftime("%Y-%m") if upper_exclusive else None
    for d in sorted(root.glob("month=*")):
        key = d.name.removeprefix("month=")
        if key == "none":
            # Строки без даты не попадут ни в одно окно, но без границ показываются как есть.
            if lo is None and hi is None:
                files.extend(str(f) for f in sorted(d.glob("*.parquet")))
            continue
        if (lo and key < lo) or (hi and key > hi):
            continue
        files.extend(str(f) for f in sorted(d.glob("*.parquet")))
    return files


def _scan_upload(
    manifest: HistoryManifest, up: UploadRef, lower: date | None, upper_exclusive: date | None
) -> pl.LazyFrame | None:
    files = _month_files(up, lower, upper_exclusive)
    if not files:
        return None
    lf = pl.scan_parquet(files, hive_partitioning=False)
    present = set(lf.collect_schema().names())
    exprs = []
    for cid, dtype in manifest.columns.items():
        target = _POLARS_TYPES[dtype]
        if cid in present:
            # Тип столбца могли поменять после загрузки: приводим при чтении, файлы не переписываем.
            exprs.append(pl.col(cid).cast(target, strict=False))
        else:
            # Столбец добавили в источник позже: в старых загрузках он пустой.
            exprs.append(pl.lit(None, dtype=target).alias(cid))
    exprs += [pl.col(c) for c in SERVICE_COLUMNS if c in present]
    return lf.select(exprs)


def history_view(
    manifest: HistoryManifest,
    columns: list[str] | None = None,
    lower: date | None = None,
    upper_exclusive: date | None = None,
) -> pl.LazyFrame:
    """Ленивая таблица действующей истории по правилу пересечения источника.

    ``columns`` — какие столбцы нужны (столбец периода и служебные добавляются всегда);
    ``lower`` и ``upper_exclusive`` — границы по столбцу периода.
    """
    pc = manifest.period_column
    ptype = manifest.columns[pc]
    active = manifest.active_uploads
    if not active:
        raise AgenError(ErrorCode.HISTORY_EMPTY, f"У источника {manifest.source_id} нет загрузок")

    policy = manifest.overlap_policy
    if policy == OverlapPolicy.ASK:
        raise AgenError(
            ErrorCode.NOT_IMPLEMENTED,
            f"Правило пересечения «ask» у источника {manifest.source_id} требует интерфейса; "
            "в командной строке выберите другое правило",
        )
    if policy == OverlapPolicy.REPLACE_ALL:
        active = [active[-1]]

    # Месяцы вне границ пропускаются, но не для merge_dedupe: там новейшая версия строки
    # может лежать в другом месяце, чем старая, и пропуск месяца изменил бы результат.
    prune = policy != OverlapPolicy.MERGE_DEDUPE
    parts: list[pl.LazyFrame] = []
    for up in active:
        lf = _scan_upload(manifest, up, lower if prune else None, upper_exclusive if prune else None)
        if lf is None:
            continue
        if policy == OverlapPolicy.REPLACE_PERIOD:
            for later in active:
                if later.seq <= up.seq:
                    continue
                if later.period.end_exclusive <= up.period.start or later.period.start >= up.period.end_exclusive:
                    continue
                covered = (pl.col(pc) >= _bound(later.period.start, ptype)) & (
                    pl.col(pc) < _bound(later.period.end_exclusive, ptype)
                )
                # Строки без даты не теряются молча: fill_null(False) оставляет их.
                lf = lf.filter(~covered.fill_null(False))
        parts.append(lf)

    lf = pl.concat(parts, how="diagonal_relaxed") if parts else _empty(manifest)

    if policy == OverlapPolicy.MERGE_DEDUPE:
        if not manifest.keys:
            raise AgenError(
                ErrorCode.SPEC_INVALID,
                f"Для правила merge_dedupe у источника {manifest.source_id} нужны ключевые столбцы (keys)",
            )
        lf = lf.sort(["_upload_seq", "_row"]).unique(subset=manifest.keys, keep="last", maintain_order=True)

    if lower is not None:
        lf = lf.filter(pl.col(pc) >= _bound(lower, ptype))
    if upper_exclusive is not None:
        lf = lf.filter(pl.col(pc) < _bound(upper_exclusive, ptype))
    if columns is not None:
        wanted = list(dict.fromkeys([pc, *columns, *SERVICE_COLUMNS]))
        lf = lf.select([c for c in wanted if c in lf.collect_schema().names()])
    return lf


def _empty(manifest: HistoryManifest) -> pl.LazyFrame:
    schema = {cid: _POLARS_TYPES[dt] for cid, dt in manifest.columns.items()}
    schema.update({"_upload_id": pl.String(), "_upload_seq": pl.Int32(), "_row": pl.Int64()})
    return pl.LazyFrame(schema=schema)
