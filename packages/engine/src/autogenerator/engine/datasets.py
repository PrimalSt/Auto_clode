"""Наборы данных из конструктора (F-301…F-307): группировка с периодами, агрегаты,
сравнение периодов, сводная таблица, доля, накопительный итог, ранг, топ-N и «Прочие».

Функции здесь работают над уже отобранными строками входа (окно и фильтр применяет
``execute``) и возвращают небольшие таблицы ``pl.DataFrame``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl

from autogenerator.contracts import (
    AggregateSpec,
    AggregationPlugin,
    DatasetSpec,
    DeriveSpec,
    Period,
    PeriodUnit,
)

from .sqlexpr import SqlTranslator, UnsupportedExpression

BUCKETS = {"day": "1d", "week": "1w", "month": "1mo", "quarter": "1q", "year": "1y"}
ORDER_COLUMNS = ("_upload_seq", "_row")


def shifted_period(period: Period, window: str) -> Period:
    """Отчётный период для сравнения: предыдущий или тот же год назад."""
    return period.years_ago(1) if window == "same_period_last_year" else period.shift(-1)


def shift_offset(period: Period, window: str) -> str:
    """На сколько сдвинуть даты периода сравнения вперёд, чтобы они совпали с текущими:
    март 2025 → март 2026, февраль → март."""
    if window == "same_period_last_year":
        return "12mo"
    match period.unit:
        case PeriodUnit.DAY:
            return "1d"
        case PeriodUnit.WEEK:
            return "1w"
        case PeriodUnit.MONTH:
            return "1mo"
        case PeriodUnit.QUARTER:
            return "3mo"
        case PeriodUnit.YEAR:
            return "1y"
    return f"{period.days}d"


def _is_temporal(dtype: Any) -> bool:
    return dtype == pl.Date or isinstance(dtype, pl.Datetime)


def group_keys(ds: DatasetSpec, schema: pl.Schema, offset: str | None = None) -> list[pl.Expr]:
    """Выражения группировки. ``offset`` сдвигает даты вперёд (для периода сравнения)."""
    keys = []
    for g in ds.group_by:
        k = pl.col(g.column)
        temporal = _is_temporal(schema.get(g.column))
        if offset and temporal:
            k = k.dt.offset_by(offset)
        if g.bucket is not None:
            k = k.dt.truncate(BUCKETS[g.bucket.value]).cast(pl.Date)
        keys.append(k.alias(g.column))
    return keys


def order_rows(lf: pl.LazyFrame, period_column: str) -> pl.LazyFrame:
    """Порядок строк для «первого» и «последнего»: по дате, затем по загрузке и строке файла."""
    names = lf.collect_schema().names()
    cols = [c for c in (period_column, *ORDER_COLUMNS) if c in names]
    return lf.sort(cols, nulls_last=True, maintain_order=True) if cols else lf


def aggregate(
    lf: pl.LazyFrame,
    ds: DatasetSpec,
    aggregates: list[tuple[AggregateSpec, AggregationPlugin]],
    period_column: str,
    offset: str | None = None,
) -> pl.DataFrame:
    schema = lf.collect_schema()
    if any(agg.needs_order for _, agg in aggregates):
        lf = order_rows(lf, period_column)
    keys = group_keys(ds, schema, offset)
    aggs = [agg.polars_expr(a.column).alias(a.output_name) for a, agg in aggregates]
    df = lf.group_by(keys, maintain_order=True).agg(aggs).collect() if keys else lf.select(aggs).collect()
    # Количество в Polars — беззнаковое целое; разница периодов с ним ушла бы в переполнение.
    return df.with_columns(pl.col(c).cast(pl.Int64) for c, t in df.schema.items() if t.is_unsigned_integer())


def change_columns(base: str, tag: str) -> list[pl.Expr]:
    prev = pl.col(f"{base}_{tag}")
    cur = pl.col(base)
    return [
        (cur - prev).alias(f"{base}_{tag}_change"),
        pl.when(prev != 0).then(cur.cast(pl.Float64) / prev - 1).otherwise(None).alias(f"{base}_{tag}_change_pct"),
    ]


def add_compare(df: pl.DataFrame, prev: pl.DataFrame, ds: DatasetSpec, tag: str) -> pl.DataFrame:
    """Столбцы сравнения: значение за другой период, разница и разница в процентах."""
    group_cols = [g.column for g in ds.group_by]
    names = [a.output_name for a in ds.aggregate]
    prev = prev.rename({n: f"{n}_{tag}" for n in names})
    if group_cols:
        prev = prev.with_columns([pl.col(c).cast(df.schema[c]) for c in group_cols])
        out = df.join(prev, on=group_cols, how="left", nulls_equal=True, maintain_order="left")
    else:
        out = pl.concat([df, prev], how="horizontal_extend")
    for n in names:
        out = out.with_columns(change_columns(n, tag))
    return out


def pivot_label(column: str, dtype: Any, bucket: PeriodUnit | None) -> pl.Expr:
    """Подпись столбца сводной таблицы: 2026-01, 2026-Q1, 2026, 2026-W05 или значение."""
    c = pl.col(column)
    if _is_temporal(dtype):
        match bucket:
            case PeriodUnit.MONTH:
                return c.dt.strftime("%Y-%m")
            case PeriodUnit.QUARTER:
                return pl.format("{}-Q{}", c.dt.year(), c.dt.quarter())
            case PeriodUnit.YEAR:
                return c.dt.year().cast(pl.String)
            case PeriodUnit.WEEK:
                return c.dt.strftime("%G-W%V")
        return c.dt.strftime("%Y-%m-%d")
    return c.cast(pl.String).fill_null("")


def pivot(df: pl.DataFrame, ds: DatasetSpec) -> pl.DataFrame:
    """Сводная таблица: значения столбца ``pivot`` становятся столбцами, остальные столбцы
    группировки — строками."""
    assert ds.pivot is not None
    bucket = next(g.bucket for g in ds.group_by if g.column == ds.pivot)
    index = [g.column for g in ds.group_by if g.column != ds.pivot]
    values = [a.output_name for a in ds.aggregate]
    df = df.sort(ds.pivot, nulls_last=True).with_columns(
        pivot_label(ds.pivot, df.schema[ds.pivot], bucket).alias(ds.pivot)
    )
    dummy = "__row"
    if not index:
        df = df.with_columns(pl.lit(0).alias(dummy))
    out = df.pivot(on=ds.pivot, index=index or [dummy], values=values, aggregate_function="first", sort_columns=False)
    if not index:
        out = out.drop(dummy)
    return out


Formula = Callable[[pl.DataFrame, str, str], pl.DataFrame]


def derive(df: pl.DataFrame, specs: list[DeriveSpec], totals: dict[str, Any], formula: Formula) -> pl.DataFrame:
    """Расчёты над набором по порядку: доля от итога (итог — по всему набору до топ-N),
    накопительный итог (в порядке строк), ранг, формула."""
    for d in specs:
        name = d.output_name
        if d.fn == "share":
            assert d.column is not None
            total = totals.get(d.column)
            if total is None:
                total = df[d.column].sum()
            df = df.with_columns(
                (pl.col(d.column).cast(pl.Float64) / total).alias(name)
                if total
                else pl.lit(None, pl.Float64).alias(name)
            )
        elif d.fn == "cumsum":
            assert d.column is not None
            df = df.with_columns(pl.col(d.column).cum_sum().alias(name))
        elif d.fn == "rank":
            assert d.column is not None
            df = df.with_columns(pl.col(d.column).rank("min", descending=d.desc).cast(pl.Int64).alias(name))
        else:
            assert d.expr is not None
            df = formula(df, name, d.expr)
    return df


def formula_column(
    df: pl.DataFrame, name: str, sql: str, fallback: Callable[[str, pl.DataFrame], pl.DataFrame]
) -> pl.DataFrame:
    """Столбец по формуле: в Polars, а если формулу не перевести — в DuckDB."""
    try:
        return df.with_columns(SqlTranslator(dict(df.schema)).expr(sql).alias(name))
    except UnsupportedExpression:
        return fallback(sql, df)


def others_mask(column: str, values: list[Any]) -> pl.Expr:
    """Строки, которые не вошли в топ: значения группы не из ``values`` (пустое значение
    группы — в «Прочих», если само не вошло в топ)."""
    present = [v for v in values if v is not None]
    cond = ~pl.col(column).is_in(present)
    return cond.fill_null(None not in values)
