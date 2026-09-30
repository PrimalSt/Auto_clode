"""Распознавание и приведение типов текстовых значений.

Учитываются русские форматы (ARCHITECTURE.md, раздел 6.1, п. 4): ``1 234,56`` (в том числе
с неразрывными пробелами U+00A0 и U+202F), ``(1 234,56)`` для отрицательных, ``29.09.2026``,
``29.09.2026 14:30``, ``31.12.2025 0:00:00``, ``да``/``нет``. Всё приведение — выражения
Polars, поэтому оно работает порциями и не держит файл в памяти.
"""

from __future__ import annotations

from datetime import time

import polars as pl

from autogenerator.contracts import DType

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"]
DATETIME_FORMATS = [
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
]
BOOL_TRUE = ["да", "true", "yes", "истина", "1", "д", "y"]
BOOL_FALSE = ["нет", "false", "no", "ложь", "0", "н", "n"]
# При выводе типа «1» и «0» — это числа, а не логические значения.
BOOL_WORDS = [v for v in BOOL_TRUE + BOOL_FALSE if not v.isdigit()]

POLARS_TYPES: dict[DType, pl.DataType] = {
    DType.STRING: pl.String(),
    DType.INT: pl.Int64(),
    DType.FLOAT: pl.Float64(),
    DType.DATE: pl.Date(),
    DType.DATETIME: pl.Datetime("us"),
    DType.BOOL: pl.Boolean(),
}

# Порог вывода типа: столько непустых значений выборки должно распознаться.
INFER_THRESHOLD = 0.98


def number_text(col: pl.Expr) -> pl.Expr:
    """Число из текста в виде, понятном ``cast(pl.Float64)``: без пробелов, с точкой."""
    s = col.str.strip_chars().str.replace_all("\u2212", "-", literal=True)
    neg = s.str.starts_with("(") & s.str.ends_with(")")
    s = s.str.strip_prefix("(").str.strip_suffix(")")
    s = s.str.replace_all("[\\s\u00a0\u202f]", "")
    has_comma = s.str.contains(",", literal=True)
    has_dot = s.str.contains(".", literal=True)
    comma_is_decimal = s.str.contains(r",\d*$")
    s = (
        pl.when(has_comma & has_dot & comma_is_decimal)
        # 1.234,56 → 1234.56
        .then(s.str.replace_all(".", "", literal=True).str.replace(",", ".", literal=True))
        # 1,234.56 → 1234.56
        .when(has_comma & has_dot)
        .then(s.str.replace_all(",", "", literal=True))
        # 1234,56 → 1234.56
        .when(has_comma)
        .then(s.str.replace(",", ".", literal=True))
        .otherwise(s)
    )
    return pl.when(neg).then(pl.lit("-") + s).otherwise(s)


def parse_float(col: pl.Expr) -> pl.Expr:
    return number_text(col).cast(pl.Float64, strict=False)


def parse_int(col: pl.Expr) -> pl.Expr:
    f = parse_float(col)
    return pl.when(f == f.round(0)).then(f.cast(pl.Int64, strict=False)).otherwise(None)


def parse_datetime(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars()
    return pl.coalesce(
        [s.str.to_datetime(f, strict=False, time_unit="us") for f in DATETIME_FORMATS]
        + [s.str.to_date(f, strict=False).cast(pl.Datetime("us")) for f in DATE_FORMATS]
    )


def parse_date(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars()
    return pl.coalesce(
        [s.str.to_date(f, strict=False) for f in DATE_FORMATS]
        + [s.str.to_datetime(f, strict=False, time_unit="us").dt.date() for f in DATETIME_FORMATS]
    )


def parse_bool(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars().str.to_lowercase()
    return pl.when(s.is_in(BOOL_TRUE)).then(True).when(s.is_in(BOOL_FALSE)).then(False).otherwise(None)


def cast_expr(col: pl.Expr, dtype: DType) -> pl.Expr:
    """Выражение приведения текстового столбца к типу источника."""
    match dtype:
        case DType.STRING:
            return col
        case DType.INT:
            return parse_int(col)
        case DType.FLOAT:
            return parse_float(col)
        case DType.DATE:
            return parse_date(col)
        case DType.DATETIME:
            return parse_datetime(col)
        case DType.BOOL:
            return parse_bool(col)
    raise ValueError(dtype)


def _share(parsed: pl.Series, total: int) -> float:
    return (parsed.len() - parsed.null_count()) / total if total else 0.0


def infer_dtype(values: pl.Series) -> tuple[DType, str | None]:
    """Тип столбца по выборке текстовых значений и формат, по которому он распознан."""
    s = values.drop_nulls()
    s = s.filter(s.str.strip_chars() != "")
    total = s.len()
    if total == 0:
        return DType.STRING, None
    frame = s.to_frame("v")
    v = pl.col("v")

    lowered = s.str.strip_chars().str.to_lowercase()
    if lowered.is_in(BOOL_WORDS).all():
        return DType.BOOL, "да/нет"

    as_float = frame.select(parse_float(v)).to_series()
    if _share(as_float, total) >= INFER_THRESHOLD:
        as_int = frame.select(parse_int(v)).to_series()
        if as_int.null_count() == as_float.null_count():
            return DType.INT, None
        return DType.FLOAT, None

    as_dt = frame.select(parse_datetime(v)).to_series()
    if _share(as_dt, total) >= INFER_THRESHOLD:
        fmt = _first_matching_format(frame, v)
        non_midnight = as_dt.drop_nulls().dt.time() != time(0, 0)
        if not bool(non_midnight.any()):
            return DType.DATE, fmt
        return DType.DATETIME, fmt

    return DType.STRING, None


def _first_matching_format(frame: pl.DataFrame, v: pl.Expr) -> str | None:
    first = frame.head(1)
    s = v.str.strip_chars()
    for f in DATETIME_FORMATS:
        if first.select(s.str.to_datetime(f, strict=False)).item() is not None:
            return f
    for f in DATE_FORMATS:
        if first.select(s.str.to_date(f, strict=False)).item() is not None:
            return f
    return None
