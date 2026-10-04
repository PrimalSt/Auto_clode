"""Периоды загрузок и действующая история (ARCHITECTURE.md, раздел 6.3).

Всё здесь — чистые функции над манифестом истории: модуль не открывает хранилище метаданных
и ничего не пишет. Загрузки неизменяемы; какие строки участвуют в истории, вычисляется при
чтении по правилу пересечения источника.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
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
    PeriodFrom,
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


def uploads_in(manifest: HistoryManifest, span: DateSpan) -> list[UploadRef]:
    """Активные загрузки, строки которых входят в действующую историю за отрезок ``span``.

    Считается по объявленным периодам, как ``coverage``: период загрузки пересекается с
    отрезком и не заменён там целиком более поздними загрузками (``replace_period`` — за
    свой период, ``replace_all`` — всё раньше себя). Строки, которые убирает
    ``merge_dedupe``, известны только по данным: такая загрузка считается прочитанной.
    """
    active = manifest.active_uploads
    rules: dict[str, OverlapPolicy] = {}
    if manifest.overlap_policy != OverlapPolicy.MERGE_DEDUPE:
        for i, up in enumerate(active):
            try:
                rules[up.id] = _rule(manifest, up, active[:i])
            except AgenError:
                rules[up.id] = OverlapPolicy.APPEND  # правило не выбрано: об этом скажет чтение истории
        last_full = max((i for i, up in enumerate(active) if rules[up.id] == OverlapPolicy.REPLACE_ALL), default=0)
        active = active[last_full:]
    # От новых к старым: ``replaced`` — объединённые периоды более поздних загрузок с
    # ``replace_period``; загрузка читается, если её часть в отрезке ими не закрыта целиком.
    out: list[UploadRef] = []
    replaced: list[tuple[date, date]] = []
    for up in reversed(active):
        start = up.period.start if span.start is None else max(up.period.start, span.start)
        end = min(up.period.end_exclusive, span.end_exclusive)
        i = bisect_right(replaced, start, key=lambda r: r[0]) - 1
        if start < end and not (i >= 0 and replaced[i][1] >= end):
            out.append(up)
        if rules.get(up.id) == OverlapPolicy.REPLACE_PERIOD:
            _add(replaced, up.period.start, up.period.end_exclusive)
    return out[::-1]


def _add(spans: list[tuple[date, date]], start: date, end: date) -> None:
    """Добавить отрезок ``[start, end)`` к объединённым отрезкам ``spans`` (по возрастанию)."""
    lo = bisect_left(spans, start, key=lambda r: r[1])
    hi = bisect_right(spans, end, key=lambda r: r[0])
    if lo < hi:
        start, end = min(start, spans[lo][0]), max(end, spans[hi - 1][1])
    spans[lo:hi] = [(start, end)]


# --- Действующая история ----------------------------------------------------------------


def _bound(value: date, dtype: DType) -> pl.Expr:
    if dtype == DType.DATETIME:
        return pl.lit(datetime.combine(value, time()), dtype=pl.Datetime("us"))
    return pl.lit(value, dtype=pl.Date())


def _month_files(up: UploadRef, lower: date | None, upper_exclusive: date | None, by_upload: bool = False) -> list[str]:
    """Файлы загрузки, пропуская месяцы вне границ: раскладка по месяцам для этого и нужна.

    ``by_upload`` — период задаётся при загрузке: все строки в периоде загрузки, и загрузка
    берётся или пропускается целиком (папка месяца могла устареть после правки периода).
    """
    root = Path(up.uri)
    files: list[str] = []
    if by_upload:
        if (lower is not None and up.period.end_exclusive <= lower) or (
            upper_exclusive is not None and up.period.start >= upper_exclusive
        ):
            return []
        return [str(f) for f in sorted(root.glob("month=*/*.parquet"))]
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
    by_upload = manifest.period_from == PeriodFrom.UPLOAD
    files = _month_files(up, lower, upper_exclusive, by_upload)
    if not files:
        return None
    lf = pl.scan_parquet(files, hive_partitioning=False)
    present = set(lf.collect_schema().names())
    exprs = []
    for cid, dtype in manifest.columns.items():
        target = _POLARS_TYPES[dtype]
        if by_upload and cid == manifest.period_column:
            # Период задан при загрузке и мог быть поправлен позже: берём его из метаданных.
            exprs.append(_bound(up.period.start, dtype).cast(target).alias(cid))
        elif cid in present:
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
    """Ленивая таблица действующей истории по правилам пересечения: источника или, при
    правиле «ask», выбранным для каждой загрузки.

    ``columns`` — какие столбцы нужны (столбец периода и служебные добавляются всегда);
    ``lower`` и ``upper_exclusive`` — границы по столбцу периода.
    """
    pc = manifest.period_column
    ptype = manifest.columns[pc]
    active = manifest.active_uploads
    if not active:
        raise AgenError(ErrorCode.HISTORY_EMPTY, f"У источника {manifest.source_id} нет загрузок")

    policy = manifest.overlap_policy
    if policy == OverlapPolicy.MERGE_DEDUPE:
        lf = _merge_dedupe(manifest, active)
    else:
        lf = _layered(manifest, active, lower, upper_exclusive)

    if lower is not None:
        lf = lf.filter(pl.col(pc) >= _bound(lower, ptype))
    if upper_exclusive is not None:
        lf = lf.filter(pl.col(pc) < _bound(upper_exclusive, ptype))
    if columns is not None:
        wanted = list(dict.fromkeys([pc, *columns, *SERVICE_COLUMNS]))
        lf = lf.select([c for c in wanted if c in lf.collect_schema().names()])
    return lf


def _merge_dedupe(manifest: HistoryManifest, active: list[UploadRef]) -> pl.LazyFrame:
    """Правило источника ``merge_dedupe``: все загрузки вместе, по ключам остаётся строка
    из самой новой. Месяцы не пропускаются: новейшая версия строки может лежать в другом
    месяце, чем старая, и пропуск месяца изменил бы результат."""
    _require_keys(manifest)
    parts = [lf for up in active if (lf := _scan_upload(manifest, up, None, None)) is not None]
    lf = pl.concat(parts, how="diagonal_relaxed") if parts else _empty(manifest)
    return lf.sort(["_upload_seq", "_row"]).unique(subset=manifest.keys, keep="last", maintain_order=True)


def _layered(
    manifest: HistoryManifest, active: list[UploadRef], lower: date | None, upper_exclusive: date | None
) -> pl.LazyFrame:
    """Загрузки по порядку: каждая ложится на более ранние по своему правилу.

    Правило источника действует на все загрузки; при правиле «ask» у каждой загрузки своё,
    выбранное при загрузке (``UploadRef.overlap_policy``). ``replace_period`` убирает из
    ранних загрузок строки с датой внутри своего периода, ``replace_all`` — ранние загрузки
    целиком, ``merge_dedupe`` — строки ранних загрузок с теми же ключами, ``append`` ничего
    не убирает.
    """
    pc = manifest.period_column
    ptype = manifest.columns[pc]
    rules = {up.id: _rule(manifest, up, active[:i]) for i, up in enumerate(active)}
    last_full = max((i for i, up in enumerate(active) if rules[up.id] == OverlapPolicy.REPLACE_ALL), default=0)
    active = active[last_full:]
    # Месяцы вне границ можно пропустить, пока ни одна загрузка не убирает строки по ключам.
    by_keys = any(rules[up.id] == OverlapPolicy.MERGE_DEDUPE for up in active[1:])
    if by_keys:
        _require_keys(manifest)
    prune = not by_keys
    scans = {
        up.id: _scan_upload(manifest, up, lower if prune else None, upper_exclusive if prune else None) for up in active
    }
    parts: list[pl.LazyFrame] = []
    for i, up in enumerate(active):
        lf = scans[up.id]
        if lf is None:
            continue
        for later in active[i + 1 :]:
            rule = rules[later.id]
            if rule == OverlapPolicy.REPLACE_PERIOD:
                if later.period.end_exclusive <= up.period.start or later.period.start >= up.period.end_exclusive:
                    continue
                covered = (pl.col(pc) >= _bound(later.period.start, ptype)) & (
                    pl.col(pc) < _bound(later.period.end_exclusive, ptype)
                )
                # Строки без даты не теряются молча: fill_null(False) оставляет их.
                lf = lf.filter(~covered.fill_null(False))
            elif rule == OverlapPolicy.MERGE_DEDUPE:
                newer = scans[later.id]
                if newer is not None:
                    lf = lf.join(newer.select(manifest.keys).unique(), on=manifest.keys, how="anti", nulls_equal=True)
        parts.append(lf)
    return pl.concat(parts, how="diagonal_relaxed") if parts else _empty(manifest)


def _rule(manifest: HistoryManifest, up: UploadRef, earlier: list[UploadRef]) -> OverlapPolicy:
    """Правило загрузки. При правиле источника «ask» выбор нужен, только если период
    загрузки пересекается с более ранними; без пересечения загрузка просто добавляется."""
    rule = manifest.policy_of(up)
    if rule != OverlapPolicy.ASK:
        return rule
    p = up.period
    if not any(e.period.start < p.end_exclusive and p.start < e.period.end_exclusive for e in earlier):
        return OverlapPolicy.APPEND
    raise AgenError(
        ErrorCode.OVERLAP_CHOICE,
        f"Загрузка {up.id} источника {manifest.source_id} пересекается с более ранними, "
        "а правило пересечения для неё не выбрано",
        hint="Выберите правило при загрузке (agen upload … --overlap replace_period) "
        "или задайте его в настройках источника.",
    )


def _require_keys(manifest: HistoryManifest) -> None:
    if not manifest.keys:
        raise AgenError(
            ErrorCode.SPEC_INVALID,
            f"Для правила merge_dedupe у источника {manifest.source_id} нужны ключевые столбцы (keys)",
        )


def _empty(manifest: HistoryManifest) -> pl.LazyFrame:
    schema = {cid: _POLARS_TYPES[dt] for cid, dt in manifest.columns.items()}
    schema.update({"_upload_id": pl.String(), "_upload_seq": pl.Int32(), "_row": pl.Int64()})
    return pl.LazyFrame(schema=schema)
