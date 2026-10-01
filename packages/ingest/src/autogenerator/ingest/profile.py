"""Профиль столбцов: пустые, уникальные, минимум, максимум, самые частые значения (F-106).

Для снимка структуры профиль считается по выборке (``exact=False``), после записи
загрузки — точно, по её Parquet: один потоковый проход по всем столбцам и по проходу на
частые значения текстовых и целых столбцов, где значений немного. На миллионах строк число
уникальных — оценка (HyperLogLog), а частые значения не считаются, если почти все значения
разные: так профиль не держит в памяти все значения столбца (10 млн строк × 30 столбцов —
около 0,8 ГБ).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl

from autogenerator.contracts import ColumnProfile, ValueCount

TOP_N = 5
EXACT_UNIQUE_ROWS = 1_000_000
"""До стольких строк число уникальных считается точно."""
TOP_MAX_UNIQUE = 100_000
"""Частые значения считаются, если уникальных не больше стольких."""
TOP_TYPES = (pl.String, pl.Boolean, pl.Int64, pl.Int32)
"""У дат и дробных чисел частые значения мало что говорят: для них — минимум и максимум."""


def _text(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S").removesuffix(" 00:00:00")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.10g}"
    return str(v)


def _profile(lf: pl.LazyFrame, columns: list[str], exact: bool) -> dict[str, ColumnProfile]:
    schema = lf.collect_schema()
    columns = [c for c in columns if c in schema]
    if not columns:
        return {}
    rows = lf.select(pl.len()).collect().item()
    exact_unique = rows <= EXACT_UNIQUE_ROWS
    aggs: list[pl.Expr] = []
    for i, c in enumerate(columns):
        col = pl.col(c)
        aggs.append(col.null_count().alias(f"n{i}"))
        aggs.append((col.n_unique() if exact_unique else col.approx_n_unique()).alias(f"u{i}"))
        if schema[c] != pl.Boolean:
            aggs += [col.min().alias(f"lo{i}"), col.max().alias(f"hi{i}")]
    stats = lf.select(aggs).collect(engine="streaming").row(0, named=True)
    out: dict[str, ColumnProfile] = {}
    top_for: list[str] = []
    for i, c in enumerate(columns):
        nulls = int(stats[f"n{i}"])
        unique = int(stats[f"u{i}"])
        # n_unique считает и пустое значение как одно из уникальных.
        if nulls and exact_unique:
            unique -= 1
        p = ColumnProfile(
            rows=rows,
            nulls=nulls,
            unique=unique,
            unique_approx=not exact_unique,
            min=_text(stats.get(f"lo{i}")),
            max=_text(stats.get(f"hi{i}")),
            exact=exact,
        )
        non_null = rows - nulls
        if non_null and schema[c] in TOP_TYPES:
            if unique <= TOP_MAX_UNIQUE and not (unique >= non_null and non_null > TOP_N):
                top_for.append(c)
            else:
                p.top_skipped = True
        out[c] = p
    for c in top_for:
        # По столбцу за проход: Parquet читает только его, а счётчик значений не больше
        # TOP_MAX_UNIQUE строк. Все столбцы разом держали бы их в памяти одновременно.
        tops = (
            lf.select(c)
            .drop_nulls()
            .group_by(c)
            .agg(pl.len().alias("_n"))
            .sort(["_n", c], descending=[True, False])
            .head(TOP_N)
            .collect(engine="streaming")
        )
        out[c].top = [ValueCount(value=_text(v) or "", count=int(n)) for v, n in tops.iter_rows()]
    return out


def profile_frame(df: pl.DataFrame, exact: bool = False) -> dict[str, ColumnProfile]:
    """Профиль таблицы в памяти — обычно выборки (``exact=False``)."""
    return _profile(df.lazy(), df.columns, exact)


def profile_upload(data_uri: str, columns: list[str]) -> dict[str, ColumnProfile]:
    """Точный профиль записанной загрузки по её Parquet."""
    files = sorted(str(f) for f in Path(data_uri).glob("month=*/*.parquet"))
    if not files:
        return {}
    return _profile(pl.scan_parquet(files, hive_partitioning=False), columns, exact=True)
