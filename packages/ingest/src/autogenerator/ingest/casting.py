"""Распознавание и приведение типов текстовых значений.

Учитываются русские форматы (ARCHITECTURE.md, раздел 6.1, п. 4): ``1 234,56`` (в том числе
с неразрывными пробелами U+00A0 и U+202F), ``(1 234,56)`` для отрицательных, ``29.09.2026``,
``29.09.2026 14:30``, ``31.12.2025 0:00:00``, ``да``/``нет``. Всё приведение — выражения
Polars, поэтому оно работает порциями и не держит файл в памяти.

Приведение порции идёт в два прохода. Быстрый проход рассчитан на то, как столбец выглядит
почти всегда: числа с запятой или точкой без смешения, даты в одном формате. Значения,
которые быстрый проход не понял, — обычно доли процента — разбираются полным способом:
оба разделителя сразу («1.234,56», «1,234.56»), целые с дробной частью «,00», все известные
форматы дат. На 10 млн строк это в десятки раз быстрее, чем полный разбор каждого значения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    "%Y-%m-%d %H:%M:%S%.f",
    "%Y-%m-%dT%H:%M:%S%.f",
]
BOOL_TRUE = ["да", "true", "yes", "истина", "1", "д", "y"]
BOOL_FALSE = ["нет", "false", "no", "ложь", "0", "н", "n"]
# При выводе типа «1» и «0» — это числа, а не логические значения.
BOOL_WORDS = [v for v in BOOL_TRUE + BOOL_FALSE if not v.isdigit()]

NBSP, NNBSP, MINUS = " ", " ", "−"
SPACES = [" ", NBSP, NNBSP, "\t"]

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


# --- полный разбор: каждое значение любым известным способом ------------------------------


def number_text(col: pl.Expr) -> pl.Expr:
    """Число из текста в виде, понятном ``cast(pl.Float64)``: без пробелов, с точкой."""
    s = col.str.strip_chars().str.replace_all(MINUS, "-", literal=True)
    neg = s.str.starts_with("(") & s.str.ends_with(")")
    s = s.str.strip_prefix("(").str.strip_suffix(")")
    s = s.str.replace_many(SPACES, [""] * len(SPACES))
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


def _date_fmt(s: pl.Expr, fmt: str, dtype: DType) -> pl.Expr:
    is_dt = "%H" in fmt
    if dtype == DType.DATE:
        return (
            s.str.to_datetime(fmt, strict=False, time_unit="us").dt.date()
            if is_dt
            else s.str.to_date(fmt, strict=False)
        )
    if is_dt:
        return s.str.to_datetime(fmt, strict=False, time_unit="us")
    return s.str.to_date(fmt, strict=False).cast(pl.Datetime("us"))


def parse_datetime(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars()
    return pl.coalesce([_date_fmt(s, f, DType.DATETIME) for f in DATETIME_FORMATS + DATE_FORMATS])


def parse_date(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars()
    return pl.coalesce([_date_fmt(s, f, DType.DATE) for f in DATE_FORMATS + DATETIME_FORMATS])


def parse_bool(col: pl.Expr) -> pl.Expr:
    s = col.str.strip_chars().str.to_lowercase()
    return pl.when(s.is_in(BOOL_TRUE)).then(True).when(s.is_in(BOOL_FALSE)).then(False).otherwise(None)


def cast_expr(col: pl.Expr, dtype: DType, fmt: str | None = None) -> pl.Expr:
    """Полное приведение текстового столбца к типу источника (каждое значение — всеми
    известными способами). ``fmt`` — формат даты из настроек столбца: если задан, даты
    читаются только по нему."""
    match dtype:
        case DType.STRING:
            return col
        case DType.INT:
            return parse_int(col)
        case DType.FLOAT:
            return parse_float(col)
        case DType.DATE:
            return _date_fmt(col.str.strip_chars(), fmt, dtype) if fmt else parse_date(col)
        case DType.DATETIME:
            return _date_fmt(col.str.strip_chars(), fmt, dtype) if fmt else parse_datetime(col)
        case DType.BOOL:
            return parse_bool(col)
    raise ValueError(dtype)


# --- быстрый проход -----------------------------------------------------------------------


def fast_expr(col: pl.Expr, dtype: DType, fmt: str | None = None) -> pl.Expr:
    """Приведение для типичного вида значений. Что не поняло, становится пустым и уходит
    в полный разбор (``cast_frame``)."""
    match dtype:
        case DType.STRING:
            return col
        case DType.INT:
            return col.cast(pl.Int64, strict=False)
        case DType.FLOAT:
            # «(1 234,56)» → «-1234.56»; скобка не по краям значения — ошибка.
            ok_brackets = ~(col.str.contains("(", literal=True) | col.str.contains(")", literal=True)) | (
                col.str.starts_with("(") & col.str.ends_with(")")
            )
            s = col.str.replace_many([*SPACES, ",", MINUS, "(", ")"], [*[""] * len(SPACES), ".", "-", "-", ""])
            return pl.when(ok_brackets).then(s.cast(pl.Float64, strict=False))
        case DType.DATE | DType.DATETIME:
            return _date_fmt(col, fmt, dtype) if fmt else pl.lit(None, dtype=POLARS_TYPES[dtype])
        case DType.BOOL:
            return parse_bool(col)
    raise ValueError(dtype)


def detect_date_format(values: pl.Series, dtype: DType = DType.DATE) -> str | None:
    """Формат, по которому читаются первые непустые значения столбца."""
    s = values.drop_nulls().str.strip_chars()
    s = s.filter(s != "").head(20)
    if s.is_empty():
        return None
    frame = s.to_frame("v")
    order = DATE_FORMATS + DATETIME_FORMATS if dtype == DType.DATE else DATETIME_FORMATS + DATE_FORMATS
    best, best_n = None, 0
    for f in order:
        n = frame.select(_date_fmt(pl.col("v"), f, dtype).is_not_null().sum()).item()
        if n > best_n:
            best, best_n = f, n
        if n == s.len():
            return f
    return best


@dataclass
class CastColumn:
    """Что привести: столбец файла ``source`` (или ``None`` — в файле его нет) к типу ``dtype``
    под именем ``id``. ``fmt`` — формат даты из настроек источника."""

    id: str
    dtype: DType
    source: str | None
    fmt: str | None = None
    detected_fmt: str | None = field(default=None, repr=False)


@dataclass
class CastOutcome:
    """Приведённая порция и ошибки в ней: ``bad`` — id столбца → маска строк с ошибкой."""

    frame: pl.DataFrame
    bad: dict[str, pl.Series] = field(default_factory=dict)


def _blank(s: pl.Series) -> pl.Series:
    return s.is_null() | (s.str.strip_chars() == "")


def cast_frame(raw: pl.DataFrame, columns: list[CastColumn]) -> CastOutcome:
    """Привести порцию текстовых столбцов к типам источника.

    Быстрый проход по всей порции, полный разбор — только для значений, которые быстрый
    не понял. Ошибка приведения — непустое значение, которое не привелось ни одним способом.
    """
    for c in columns:
        # Формат запоминается от порции к порции: в выгрузке он почти всегда один.
        if c.dtype in (DType.DATE, DType.DATETIME) and c.source is not None and not c.fmt and c.detected_fmt is None:
            c.detected_fmt = detect_date_format(raw.get_column(c.source), c.dtype)
    exprs = []
    for c in columns:
        if c.source is None:
            exprs.append(pl.lit(None, dtype=POLARS_TYPES[c.dtype]).alias(c.id))
        else:
            exprs.append(fast_expr(pl.col(c.source), c.dtype, c.fmt or c.detected_fmt).alias(c.id))
    typed = raw.lazy().select(exprs).collect()

    bad: dict[str, pl.Series] = {}
    fixes: list[pl.Series] = []
    for c in columns:
        if c.source is None or c.dtype == DType.STRING:
            continue
        src = raw.get_column(c.source)
        got = typed.get_column(c.id)
        if got.null_count() == src.null_count():
            continue
        missed = src.is_not_null() & got.is_null()
        idx = missed.arg_true()
        sub = src.gather(idx)
        full = sub.to_frame("v").select(cast_expr(pl.col("v"), c.dtype, c.fmt).alias("v")).get_column("v")
        if c.fmt is None and c.dtype in (DType.DATE, DType.DATETIME) and full.null_count() == full.len():
            c.detected_fmt = None  # формат сменился: в следующей порции определить заново
        if full.null_count() < full.len():
            fixes.append(got.scatter(idx, full.cast(got.dtype)).alias(c.id))
        still = full.is_null() & ~_blank(sub)
        if still.any():
            mask = pl.Series(c.id, [False] * raw.height, dtype=pl.Boolean)
            bad[c.id] = mask.scatter(idx.filter(still), True)
    if fixes:
        typed = typed.with_columns(fixes)
    return CastOutcome(typed, bad)


# --- вывод типа по выборке -------------------------------------------------------------------


def _share(parsed: pl.Series, total: int) -> float:
    return (parsed.len() - parsed.null_count()) / total if total else 0.0


def infer_dtype(values: pl.Series) -> tuple[DType, str | None]:
    """Тип столбца по выборке текстовых значений и формат, по которому он распознан."""
    dtype, fmt, _ = infer_dtype_share(values)
    return dtype, fmt


def infer_dtype_share(values: pl.Series) -> tuple[DType, str | None, float | None]:
    """Тип, формат и доля непустых значений выборки, которые к нему привелись."""
    s = values.drop_nulls()
    s = s.filter(s.str.strip_chars() != "")
    total = s.len()
    if total == 0:
        return DType.STRING, None, None
    frame = s.to_frame("v")
    v = pl.col("v")

    lowered = s.str.strip_chars().str.to_lowercase()
    if lowered.is_in(BOOL_WORDS).all():
        return DType.BOOL, "да/нет", 1.0

    as_float = frame.select(parse_float(v)).to_series()
    share = _share(as_float, total)
    if share >= INFER_THRESHOLD:
        # Ведущие нули («0274…», «007») — признак кода: число их потеряет.
        if bool(s.str.strip_chars().str.contains(r"^0\d").any()):
            return DType.STRING, None, None
        as_int = frame.select(parse_int(v)).to_series()
        if as_int.null_count() == as_float.null_count():
            return DType.INT, None, share
        return DType.FLOAT, None, share

    as_dt = frame.select(parse_datetime(v)).to_series()
    share = _share(as_dt, total)
    if share >= INFER_THRESHOLD:
        non_midnight = as_dt.drop_nulls().dt.time() != time(0, 0)
        if not bool(non_midnight.any()):
            return DType.DATE, detect_date_format(s, DType.DATE), share
        return DType.DATETIME, detect_date_format(s, DType.DATETIME), share

    return DType.STRING, None, None
