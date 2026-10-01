"""Покрытие истории, пересечения новой загрузки с прежними и тип периода по датам
(ARCHITECTURE.md, раздел 6.3; экран истории — F-156)."""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from pathlib import Path

import polars as pl

from autogenerator.contracts import (
    CoverageCell,
    CoverageReport,
    CoverageState,
    DateSpan,
    DType,
    HistoryManifest,
    Period,
    PeriodUnit,
    UploadRef,
)
from autogenerator.contracts.periods import unit_shift, unit_start

from .view import _bound, coverage

MAX_CELLS = 400
"""Шкала длиннее стольких единиц укрупняется: дни → недели → месяцы → кварталы → годы."""

_COARSER = [PeriodUnit.DAY, PeriodUnit.WEEK, PeriodUnit.MONTH, PeriodUnit.QUARTER, PeriodUnit.YEAR]


def _intersects(a: Period, start: date, end_exclusive: date) -> bool:
    return a.start < end_exclusive and start < a.end_exclusive


def overlapping_uploads(manifest: HistoryManifest, period: Period) -> list[UploadRef]:
    """Активные загрузки, чей период пересекается с ``period``, по порядку загрузки."""
    return [u for u in manifest.active_uploads if _intersects(u.period, period.start, period.end_exclusive)]


def _overlaps(uploads: list[UploadRef]) -> list[DateSpan]:
    """Отрезки, покрытые двумя и более загрузками (проход по границам)."""
    events: list[tuple[date, int]] = []
    for u in uploads:
        events += [(u.period.start, 1), (u.period.end_exclusive, -1)]
    events.sort(key=lambda e: (e[0], e[1]))
    out: list[DateSpan] = []
    depth = 0
    opened: date | None = None
    for d, step in events:
        depth += step
        if depth >= 2 and opened is None:
            opened = d
        elif depth < 2 and opened is not None:
            if d > opened:
                if out and out[-1].start is not None and out[-1].end_exclusive == opened:
                    out[-1] = DateSpan(start=out[-1].start, end_exclusive=d)
                else:
                    out.append(DateSpan(start=opened, end_exclusive=d))
            opened = None
    return out


def _scale_unit(manifest: HistoryManifest, first: date, end: date, max_cells: int) -> PeriodUnit:
    unit = manifest.period_type if manifest.period_type != PeriodUnit.RANGE else PeriodUnit.DAY
    i = _COARSER.index(unit)
    while i < len(_COARSER) - 1:
        start = unit_start(first, _COARSER[i])
        n = 0
        while start < end and n <= max_cells:
            start = unit_shift(start, _COARSER[i], 1)
            n += 1
        if n <= max_cells:
            break
        i += 1
    return _COARSER[i]


def coverage_report(
    manifest: HistoryManifest, unit: PeriodUnit | None = None, max_cells: int = MAX_CELLS
) -> CoverageReport:
    """Шкала покрытия: какие единицы покрыты, где пропуски, где загрузки накладываются.

    Единица шкалы — тип периода источника (для «range» — дни), а если шкала получается
    длиннее ``max_cells`` единиц — более крупная. Единица покрыта, если в неё заходит период
    хотя бы одной активной загрузки; наложение — две и более загрузки.
    """
    active = manifest.active_uploads
    spans = coverage(manifest)
    gaps = [
        DateSpan(start=a.end_exclusive, end_exclusive=b.start)
        for a, b in pairwise(spans)
        if b.start is not None and a.end_exclusive < b.start
    ]
    if not active:
        return CoverageReport(unit=unit or manifest.period_type, spans=[], gaps=[], overlaps=[], cells=[])
    first = min(u.period.start for u in active)
    end = max(u.period.end_exclusive for u in active)
    scale = unit if unit is not None and unit != PeriodUnit.RANGE else _scale_unit(manifest, first, end, max_cells)
    cells: list[CoverageCell] = []
    start = unit_start(first, scale)
    while start < end:
        nxt = unit_shift(start, scale, 1)
        ids = [u.id for u in active if _intersects(u.period, start, nxt)]
        state = CoverageState.GAP if not ids else CoverageState.OVERLAP if len(ids) > 1 else CoverageState.COVERED
        cells.append(CoverageCell(period=Period(start=start, end_exclusive=nxt, unit=scale), uploads=ids, state=state))
        start = nxt
    return CoverageReport(spans=spans, gaps=gaps, overlaps=_overlaps(active), unit=scale, cells=cells)


def guess_period_type(first: date, last: date) -> PeriodUnit:
    """Тип периода по датам первой выгрузки: самая мелкая календарная единица, в которую
    они укладываются целиком; иначе — произвольный диапазон. Пользователь может поменять."""
    for unit in (PeriodUnit.DAY, PeriodUnit.MONTH, PeriodUnit.QUARTER):
        if unit_start(first, unit) == unit_start(last, unit):
            return unit
    if first.year == last.year and first.month == 1 and last.month == 12:
        return PeriodUnit.YEAR
    return PeriodUnit.RANGE


def rows_outside(data_uri: str, period_column: str, dtype: DType, period: Period) -> int:
    """Строки загрузки с датой вне объявленного периода (например, если период типа
    «range» задан вручную уже, чем даты в файле). Такие строки остаются в загрузке, но
    правило ``replace_period`` их не заменяет."""
    files = sorted(str(f) for f in Path(data_uri).glob("month=*/*.parquet") if f.parent.name != "month=none")
    if not files:
        return 0
    col = pl.col(period_column)
    inside = (col >= _bound(period.start, dtype)) & (col < _bound(period.end_exclusive, dtype))
    lf = pl.scan_parquet(files, hive_partitioning=False)
    return int(lf.select((col.is_not_null() & ~inside).sum()).collect().item())
