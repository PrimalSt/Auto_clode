"""Встроенные шаги обработки (ARCHITECTURE.md, раздел 6.4; PRD F-201…F-206, F-209).

Здесь шаги над одной таблицей: ``filter``, ``time_filter``, ``dedupe``, ``sort``, ``select``,
``rename``, ``cast``, ``formula``. Объединение — в ``join``, шаги «SQL» и «Python» — в ``code``.

Операции над всей таблицей (удаление дубликатов, сортировка) на больших данных
(``ctx.large``) шаг отдаёт DuckDB через ``ctx.sql``: он считает с выгрузкой на диск.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autogenerator.contracts import (
    AgenError,
    ColumnTypes,
    DType,
    ErrorCode,
    ExpressionTools,
    Issue,
    PeriodUnit,
    SchemaTools,
    SortSpec,
    StepContext,
    StepPlugin,
)
from autogenerator.contracts.periods import unit_shift, unit_start

SERVICE = ("_upload_id", "_upload_seq", "_row")


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def q(name: str) -> str:
    """Имя столбца в SQL."""
    return '"' + name.replace('"', '""') + '"'


def _is_service(c: str) -> bool:
    return c.startswith("_")


def _cast_value(value: Any, dtype: Any) -> Any:
    """Значение из сценария (YAML) — к типу столбца: строка 2026-01-31 к дате и т. п."""
    if isinstance(value, str):
        v = value.strip()
        if dtype == pl.Date:
            return date.fromisoformat(v[:10])
        if isinstance(dtype, pl.Datetime):
            return datetime.fromisoformat(v)
        if dtype.is_integer():
            return int(v)
        if dtype.is_float():
            return float(v.replace(",", "."))
    if isinstance(value, date) and not isinstance(value, datetime) and isinstance(dtype, pl.Datetime):
        return datetime.combine(value, dtime())
    if isinstance(value, datetime) and dtype == pl.Date:
        return value.date()
    return value


def _lit(value: Any, dtype: Any, column: str) -> pl.Expr:
    try:
        return pl.lit(_cast_value(value, dtype))
    except (ValueError, TypeError) as e:
        raise AgenError(ErrorCode.EXPRESSION, f"значение «{value}» не подходит к столбцу «{column}» ({dtype})") from e


# --- filter -------------------------------------------------------------------------


Op = Literal[
    "eq", "ne", "in", "not_in", "contains", "starts_with", "gt", "ge", "lt", "le", "between", "is_null", "not_null"
]
OP_TEXT = {
    "eq": "=",
    "ne": "≠",
    "in": "в списке",
    "not_in": "не в списке",
    "contains": "содержит",
    "starts_with": "начинается с",
    "gt": ">",
    "ge": "≥",
    "lt": "<",
    "le": "≤",
    "between": "между",
    "is_null": "пусто",
    "not_null": "не пусто",
}


class Condition(_Params):
    """Условие по значению столбца (F-202)."""

    column: str
    op: Op = "eq"
    value: Any = None

    @model_validator(mode="after")
    def _check(self) -> Condition:
        if self.op in ("is_null", "not_null"):
            if self.value is not None:
                raise ValueError(f"условию «{OP_TEXT[self.op]}» значение не нужно")
        elif self.op in ("in", "not_in"):
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"условию «{OP_TEXT[self.op]}» нужен непустой список значений")
        elif self.op == "between":
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("условию «между» нужны два значения: [от, до]")
        elif self.value is None or isinstance(self.value, list | dict):
            raise ValueError(f"условию «{OP_TEXT[self.op]}» нужно одно значение")
        return self

    def expr(self, schema: pl.Schema) -> pl.Expr:
        if self.column not in schema:
            known = ", ".join(c for c in schema.names() if not _is_service(c))
            raise AgenError(ErrorCode.EXPRESSION, f"нет столбца «{self.column}» (есть: {known})")
        c = pl.col(self.column)
        dtype = schema[self.column]
        v = self.value
        match self.op:
            case "is_null":
                return c.is_null()
            case "not_null":
                return c.is_not_null()
            case "in" | "not_in":
                values = [_cast_value(x, dtype) for x in v]
                e = c.is_in(pl.Series(values, dtype=dtype, strict=False).implode())
                # NULL IN (…) и NULL NOT IN (…) — пусто, как в SQL: строка не проходит.
                e = pl.when(c.is_null()).then(None).otherwise(e)
                return ~e if self.op == "not_in" else e
            case "contains":
                return c.cast(pl.String).str.contains(str(v), literal=True)
            case "starts_with":
                return c.cast(pl.String).str.starts_with(str(v))
            case "between":
                return (c >= _lit(v[0], dtype, self.column)) & (c <= _lit(v[1], dtype, self.column))
        rhs = _lit(v, dtype, self.column)
        return {"eq": c == rhs, "ne": c != rhs, "gt": c > rhs, "ge": c >= rhs, "lt": c < rhs, "le": c <= rhs}[self.op]

    def describe(self) -> str:
        if self.op in ("is_null", "not_null"):
            return f"{self.column} {OP_TEXT[self.op]}"
        return f"{self.column} {OP_TEXT[self.op]} {self.value}"


class FilterParams(_Params):
    where: str | None = Field(None, description="Условие на SQL, например amount > 0 AND region <> 'Прочие'")
    conditions: list[Condition] = Field(default_factory=list, description="Условия по значениям столбцов")
    combine: Literal["and", "or"] = Field("and", description="Как сочетать условия: И или ИЛИ")

    @model_validator(mode="after")
    def _check(self) -> FilterParams:
        if (self.where is None) == (not self.conditions):
            raise ValueError("нужно одно из двух: where (условие на SQL) или conditions (условия по значениям)")
        return self


class FilterStep(StepPlugin):
    """Фильтр строк. Пустой результат условия (NULL) строку не пропускает, как в SQL.

    При запуске (не в превью) шаг с условиями по значениям предупреждает, если в самой
    новой загрузке условие не нашло ни одной строки или в столбце появились значения,
    которых не было в прошлых загрузках (F-209)."""

    name = "filter"
    title = "Фильтр"
    Params = FilterParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        if params.where is not None:
            return tools.columns_in(params.where)
        return {c.column for c in params.conditions}

    def condition(self, params: Any, schema: pl.Schema) -> pl.Expr:
        parts = [c.expr(schema) for c in params.conditions]
        e = parts[0]
        for p in parts[1:]:
            e = (e & p) if params.combine == "and" else (e | p)
        return e

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        if params.where is not None:
            return ctx.filter(lf, params.where)
        schema = lf.collect_schema()
        cond = self.condition(params, schema)
        if not ctx.preview and "_upload_seq" in schema:
            self.check_new_upload(lf, params, schema, ctx)
        return lf.filter(cond.fill_null(False))

    def check_new_upload(self, lf: pl.LazyFrame, params: Any, schema: pl.Schema, ctx: StepContext) -> None:
        """F-209: условие в новой выгрузке без строк, новые значения в отфильтрованном столбце."""
        lo, hi = lf.select(pl.col("_upload_seq").min(), pl.col("_upload_seq").max().alias("hi")).collect().row(0)
        if hi is None:
            return
        newest = pl.col("_upload_seq") == hi
        checks = [lf.select((self.condition(params, schema).fill_null(False) & newest).sum())]
        value_conds = [c for c in params.conditions if c.op in ("eq", "ne", "in", "not_in")] if lo != hi else []
        for c in value_conds:
            col = pl.col(c.column)
            checks.append(
                lf.group_by(col)
                .agg(newest.all().alias("only_new"))
                .filter(pl.col("only_new") & col.is_not_null())
                .select(col)
                .sort(col)
                .head(6)
            )
        results = pl.collect_all(checks)
        if results[0].item() == 0:
            joiner = " и " if params.combine == "and" else " или "
            what = joiner.join(c.describe() for c in params.conditions)
            ctx.warn(f"в новой выгрузке нет ни одной строки с условием «{what}»")
        for c, res in zip(value_conds, results[1:], strict=True):
            listed = {str(x) for x in (c.value if isinstance(c.value, list) else [c.value])}
            new = [str(v) for v in res[c.column].to_list() if str(v) not in listed]
            if new:
                more = " и другие" if len(new) > 5 else ""
                ctx.warn(
                    f"в столбце «{c.column}» новой выгрузки появились значения, которых не было раньше: "
                    f"{', '.join(new[:5])}{more}; проверьте условие «{c.describe()}»"
                )


# --- time_filter --------------------------------------------------------------------


Relative = Literal[
    "current_month",
    "previous_month",
    "current_quarter",
    "previous_quarter",
    "current_year",
    "previous_year",
    "year_to_date",
    "quarter_to_date",
]
_RELATIVE_UNITS = {
    "month": PeriodUnit.MONTH,
    "quarter": PeriodUnit.QUARTER,
    "year": PeriodUnit.YEAR,
}


class TimeFilterParams(_Params):
    column: str | None = Field(None, description="Столбец дат; по умолчанию — столбец периода входа")
    start: date | None = Field(None, description="Абсолютный диапазон: первый день")
    end: date | None = Field(None, description="Абсолютный диапазон: последний день, включительно")
    last: int | None = Field(None, ge=1, description="Последние N единиц, считая ту, где точка отсчёта")
    unit: Literal["day", "week", "month", "quarter", "year"] = "month"
    period: Relative | None = Field(None, description="Относительный период: current_month, previous_quarter, …")

    @model_validator(mode="after")
    def _check(self) -> TimeFilterParams:
        modes = [
            m for m, on in (("start/end", self.start or self.end), ("last", self.last), ("period", self.period)) if on
        ]
        if len(modes) != 1:
            raise ValueError("задайте одно из: start и end (диапазон дат), last (последние N единиц) или period")
        if self.start and self.end and self.end < self.start:
            raise ValueError("end раньше start")
        return self

    def span(self, anchor: date) -> tuple[date | None, date | None]:
        """Отрезок ``[start, end_exclusive)``; ``anchor`` — последний день отсчёта."""
        after = anchor + timedelta(days=1)
        if self.last is not None:
            if self.unit == "day":
                return anchor - timedelta(days=self.last - 1), after
            unit = PeriodUnit(self.unit)
            return unit_shift(unit_start(anchor, unit), unit, -(self.last - 1)), after
        if self.period is not None:
            which, unit_name = self.period.split("_", 1)
            if unit_name == "to_date":
                unit_name, which = which, "current"
            unit = _RELATIVE_UNITS[unit_name]
            start = unit_start(anchor, unit)
            if which == "previous":
                return unit_shift(start, unit, -1), start
            return start, after
        return self.start, self.end + timedelta(days=1) if self.end else None


class TimeFilterStep(StepPlugin):
    """Фильтр по времени для всей обработки (F-203): абсолютный диапазон или период
    относительно точки отсчёта — конца отчётного периода или даты запуска."""

    name = "time_filter"
    title = "Фильтр по времени"
    Params = TimeFilterParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return {params.column} if params.column else set()

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        if params.column and schema.get(params.column) not in (DType.DATE, DType.DATETIME, None):
            raise AgenError(ErrorCode.EXPRESSION, f"«{params.column}» — не столбец дат ({schema.get(params.column)})")
        return schema

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        column = params.column or ctx.period_column
        dtype = lf.collect_schema().get(column)
        start, end = params.span(ctx.anchor)

        def bound(d: date) -> pl.Expr:
            return pl.lit(datetime.combine(d, dtime())) if isinstance(dtype, pl.Datetime) else pl.lit(d)

        cond = pl.lit(True)
        if start is not None:
            cond = cond & (pl.col(column) >= bound(start))
        if end is not None:
            cond = cond & (pl.col(column) < bound(end))
        return lf.filter(cond.fill_null(False))


# --- dedupe -------------------------------------------------------------------------


class DedupeParams(_Params):
    by: list[str] | None = Field(None, min_length=1, description="Столбцы, по которым ищутся дубликаты; пусто — все")
    keep: Literal["first", "last", "max", "min"] = Field(
        "last",
        description="Какую строку оставить: из более ранней (first) или более поздней (last) загрузки, "
        "с наибольшим (max) или наименьшим (min) значением столбца column",
    )
    column: str | None = Field(None, description="Столбец для keep: max или min")
    depth: int | None = Field(
        None, ge=1, description="Глубина поиска: дубликаты ищутся в последних N периодах; пусто — во всей истории"
    )

    @model_validator(mode="after")
    def _check(self) -> DedupeParams:
        if self.keep in ("max", "min") and not self.column:
            raise ValueError(f"для keep: {self.keep} нужен столбец column")
        if self.keep in ("first", "last") and self.column:
            raise ValueError("column задаётся только для keep: max или min")
        return self


class DedupeStep(StepPlugin):
    """Удаление дубликатов по всей истории (F-201): «последняя» — из самой новой загрузки, а
    внутри загрузки — ниже по файлу. На больших данных — в DuckDB. Первая и последняя строки —
    группировкой: у каждой строки номер ``_upload_seq · 2⁴⁰ + _row``, остаются строки с
    наибольшим (наименьшим) номером в группе ключей; это быстрее оконной функции (на 50 млн
    строк — 30 с вместо 50 с). Если строки до этого размножил шаг объединения, у копий один
    номер и остаются все копии. Наибольшее и наименьшее значение столбца —
    ``QUALIFY row_number() OVER (PARTITION BY ключи ORDER BY столбец, _upload_seq, _row) = 1``."""

    name = "dedupe"
    title = "Удалить дубликаты"
    Params = DedupeParams
    row_local = False

    def lookback(self, params: Any) -> int | None:
        return int(params.depth) if params.depth else None

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.by or []) | ({params.column} if params.column else set())

    def reads_all_columns(self, params: Any, tools: ExpressionTools) -> bool:
        return params.by is None

    def key_columns(self, params: Any) -> dict[str, list[str]]:
        return {"data": list(params.by) if params.by else ["*"]}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        names = lf.collect_schema().names()
        by = list(params.by) if params.by else [c for c in names if not _is_service(c)]
        missing = [c for c in [*by, *([params.column] if params.column else [])] if c not in names]
        if missing:
            raise AgenError(ErrorCode.EXPRESSION, f"нет столбца «{missing[0]}»")
        service = [c for c in ("_upload_seq", "_row") if c in names]
        # Какая строка группы остаётся: первая в этом порядке.
        if params.keep == "first":
            order = [(c, False) for c in service]
        elif params.keep == "last":
            order = [(c, True) for c in service]
        else:
            order = [(params.column, params.keep == "max"), *((c, True) for c in service)]
        if ctx.large and params.keep in ("first", "last") and len(service) == 2:
            rid = 'CAST("_upload_seq" AS BIGINT) * 1099511627776 + "_row"'
            fn = "max" if params.keep == "last" else "min"
            query = (
                f"SELECT * FROM data WHERE {rid} IN (SELECT {fn}({rid}) FROM data GROUP BY "
                f'{", ".join(q(c) for c in by)}) ORDER BY "_upload_seq", "_row"'
            )
            return ctx.sql(query, {"data": lf})
        if ctx.large:
            parts = ", ".join(f"{q(c)} {'DESC' if desc else 'ASC'} NULLS LAST" for c, desc in order) or "1"
            restore = f" ORDER BY {', '.join(q(c) for c in service)}" if service else ""
            query = (
                f"SELECT * FROM data QUALIFY row_number() OVER (PARTITION BY {', '.join(q(c) for c in by)} "
                f"ORDER BY {parts}) = 1{restore}"
            )
            return ctx.sql(query, {"data": lf})
        if params.keep in ("first", "last"):
            # Порядок строк сохраняется: история и так идёт по загрузкам и строкам файла.
            if service:
                lf = lf.sort(service, maintain_order=True)
            return lf.unique(subset=by, keep=params.keep, maintain_order=True)
        lf = lf.sort([c for c, _ in order], descending=[d for _, d in order], nulls_last=True, maintain_order=True)
        lf = lf.unique(subset=by, keep="first", maintain_order=True)
        return lf.sort(service, maintain_order=True) if service else lf


# --- sort ---------------------------------------------------------------------------


class SortParams(_Params):
    by: list[SortSpec] = Field(min_length=1, description='Столбцы сортировки: "date", "-amount" (по убыванию)')


class SortStep(StepPlugin):
    """Сортировка (F-204). Пустые значения — в конце. Порядок строк не меняет их набор,
    поэтому нижняя граница истории через шаг проталкивается."""

    name = "sort"
    title = "Сортировка"
    Params = SortParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return {s.column for s in params.by}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        if ctx.large:
            parts = ", ".join(f"{q(s.column)} {'DESC' if s.desc else 'ASC'} NULLS LAST" for s in params.by)
            return ctx.sql(f"SELECT * FROM data ORDER BY {parts}", {"data": lf})
        return lf.sort(
            [s.column for s in params.by], descending=[s.desc for s in params.by], nulls_last=True, maintain_order=True
        )


# --- select / rename / cast -------------------------------------------------------------


class SelectParams(_Params):
    columns: list[str] | None = Field(None, min_length=1, description="Столбцы, которые остаются")
    drop: list[str] | None = Field(None, min_length=1, description="Столбцы, которые удаляются")

    @model_validator(mode="after")
    def _check(self) -> SelectParams:
        if (self.columns is None) == (self.drop is None):
            raise ValueError("задайте columns (какие столбцы оставить) или drop (какие удалить)")
        return self


class SelectStep(StepPlugin):
    """Оставить или удалить столбцы (F-205). Столбец периода и служебные остаются всегда."""

    name = "select"
    title = "Выбрать столбцы"
    Params = SelectParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.columns or params.drop or [])

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        # Столбец периода шаг тоже оставляет, но его имени разбор не знает: он есть в схеме
        # источника, и движок проверяет его отдельно.
        if params.columns is not None:
            keep = set(params.columns)
            return {c: t for c, t in schema.items() if c in keep or _is_service(c)}
        return {c: t for c, t in schema.items() if c not in set(params.drop)}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        names = lf.collect_schema().names()
        listed = params.columns or params.drop
        missing = [c for c in listed if c not in names]
        if missing:
            raise AgenError(ErrorCode.EXPRESSION, f"нет столбца «{missing[0]}»")
        if params.drop is not None:
            if ctx.period_column in params.drop:
                raise AgenError(ErrorCode.EXPRESSION, f"столбец периода «{ctx.period_column}» удалить нельзя")
            return lf.drop(params.drop)
        keep = [ctx.period_column, *params.columns, *(c for c in names if _is_service(c))]
        return lf.select([c for c in dict.fromkeys(keep) if c in names])


class RenameParams(_Params):
    columns: dict[str, str] = Field(min_length=1, description="Старое имя → новое")


class RenameStep(StepPlugin):
    """Переименование столбцов (F-205). Столбец периода и служебные не переименовываются."""

    name = "rename"
    title = "Переименовать"
    Params = RenameParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.columns)

    def columns_written(self, params: Any) -> set[str]:
        return set(params.columns.values())

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        out: ColumnTypes = {}
        for c, t in schema.items():
            out[params.columns.get(c, c)] = t
        if len(out) != len(schema):
            raise AgenError(ErrorCode.EXPRESSION, "после переименования имена столбцов повторяются")
        return out

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        for old in params.columns:
            if old == ctx.period_column or _is_service(old):
                raise AgenError(ErrorCode.EXPRESSION, f"столбец «{old}» переименовать нельзя")
        return lf.rename(params.columns)


CastType = Literal["string", "int", "float", "date", "datetime", "bool"]
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d")
DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M")
TRUE_WORDS = ["true", "1", "да", "yes", "истина", "y"]
FALSE_WORDS = ["false", "0", "нет", "no", "ложь", "n"]


class CastParams(_Params):
    columns: dict[str, CastType] = Field(min_length=1, description="Столбец → новый тип")
    formats: dict[str, str] = Field(default_factory=dict, description="Формат даты для столбца, например %d.%m.%Y")


def cast_expr(column: str, src: Any, target: CastType, fmt: str | None) -> pl.Expr:
    c = pl.col(column)
    if target == "string":
        return c.cast(pl.String)
    if src == pl.String:
        text = c.str.strip_chars()
        if target in ("int", "float"):
            num = text.str.replace_all(r"[\s  ']", "").str.replace(",", ".").cast(pl.Float64, strict=False)
            if target == "float":
                return num
            return pl.when(num == num.floor()).then(num).otherwise(None).cast(pl.Int64, strict=False)
        if target == "date":
            parts = [text.str.to_date(f, strict=False) for f in ([fmt] if fmt else DATE_FORMATS)]
            return pl.coalesce(parts) if len(parts) > 1 else parts[0]
        if target == "datetime":
            formats = [fmt] if fmt else (*DATETIME_FORMATS, *DATE_FORMATS)
            return pl.coalesce([text.str.to_datetime(f, time_unit="us", strict=False) for f in formats])
        low = text.str.to_lowercase()
        return pl.when(low.is_in(TRUE_WORDS)).then(True).when(low.is_in(FALSE_WORDS)).then(False).otherwise(None)
    if target == "int":
        if src.is_float():
            # Как CAST в DuckDB: дробное число округляется до ближайшего чётного.
            return c.round(0, mode="half_to_even").cast(pl.Int64, strict=False)
        return c.cast(pl.Int64, strict=False)
    if target == "float":
        return c.cast(pl.Float64, strict=False)
    if target == "date":
        return c.cast(pl.Date, strict=False)
    if target == "datetime":
        return c.cast(pl.Datetime("us"), strict=False)
    return c.cast(pl.Boolean, strict=False)


_DTYPES: dict[str, DType] = {
    "string": DType.STRING,
    "int": DType.INT,
    "float": DType.FLOAT,
    "date": DType.DATE,
    "datetime": DType.DATETIME,
    "bool": DType.BOOL,
}


class CastStep(StepPlugin):
    """Смена типа столбцов (F-205). Числа из текста понимаются с пробелами между разрядами и
    запятой, даты — в форматах ГГГГ-ММ-ДД и ДД.ММ.ГГГГ (или в заданном). Нераспознанное
    значение становится пустым; при запуске шаг сообщает, сколько таких."""

    name = "cast"
    title = "Сменить тип"
    Params = CastParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.columns)

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        return {c: (_DTYPES[params.columns[c]] if c in params.columns else t) for c, t in schema.items()}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        schema = lf.collect_schema()
        if ctx.period_column in params.columns and params.columns[ctx.period_column] not in ("date", "datetime"):
            raise AgenError(ErrorCode.EXPRESSION, f"столбец периода «{ctx.period_column}» должен остаться датой")
        exprs = {}
        for col, target in params.columns.items():
            if col not in schema:
                raise AgenError(ErrorCode.EXPRESSION, f"нет столбца «{col}»")
            exprs[col] = cast_expr(col, schema[col], target, params.formats.get(col))
        if not ctx.preview:
            lost = (
                lf.select([(pl.col(c).is_not_null() & e.is_null()).sum().alias(c) for c, e in exprs.items()])
                .collect()
                .row(0, named=True)
            )
            for c, n in lost.items():
                if n:
                    ctx.warn(f"«{c}»: {n} значений не распознаны как {params.columns[c]} и стали пустыми")
        return lf.with_columns([e.alias(c) for c, e in exprs.items()])


# --- formula ------------------------------------------------------------------------


class FormulaParams(_Params):
    column: str = Field(description="id нового или заменяемого столбца")
    expr: str = Field(description="Выражение на SQL (диалект DuckDB), например amount / 1.2")


class FormulaStep(StepPlugin):
    """Вычисляемый столбец по формуле (F-206). Формулу переводит в Polars движок, а если в
    ней функция, которой нет в таблице перевода, её считает DuckDB с тем же результатом."""

    name = "formula"
    title = "Формула"
    Params = FormulaParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return tools.columns_in(params.expr)

    def columns_written(self, params: Any) -> set[str]:
        return {params.column}

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        return {**schema, params.column: tools.expr_type(params.expr, schema)}

    def check(self, params: Any, tools: ExpressionTools) -> list[Issue]:
        if _is_service(params.column):
            return [
                Issue(level="error", message=f"имя столбца «{params.column}» начинается с «_»: такие имена служебные")
            ]
        return []

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        return ctx.with_column(lf, params.column, params.expr)
