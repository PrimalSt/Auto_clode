"""Движок проверяется со своими маленькими плагинами и историей в памяти: без steps-std,
history и файлов выгрузок. Ошибка в другом модуле эти тесты не ломает."""

from datetime import date
from typing import Any

import polars as pl
import pytest
from pydantic import BaseModel, ConfigDict, Field

from autogenerator.contracts import (
    AggregationPlugin,
    DateSpan,
    DType,
    Period,
    StepPlugin,
    UploadRef,
    WindowPlugin,
)
from autogenerator.engine import InputSchema
from autogenerator.plugin_host import PluginRegistry


class ReportPeriod(WindowPlugin):
    name = "report_period"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return period.span


class Previous(WindowPlugin):
    name = "previous_period"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return period.shift(-1).span


class LastYear(WindowPlugin):
    name = "same_period_last_year"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return period.years_ago(1).span


class LastNParams(BaseModel):
    n: int


class LastN(WindowPlugin):
    name = "last_n"
    Params = LastNParams

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return DateSpan(start=period.shift(-(params.n - 1)).start, end_exclusive=period.end_exclusive)


class Everything(WindowPlugin):
    name = "all"

    def resolve(self, period: Period, params: Any) -> DateSpan:
        return DateSpan(start=None, end_exclusive=period.end_exclusive)


class Sum(AggregationPlugin):
    name = "sum"

    def polars_expr(self, column: str | None) -> pl.Expr:
        return pl.col(column).sum()

    def sql(self, column: str | None) -> str:
        return f"SUM({column})"


class Last(AggregationPlugin):
    name = "last"
    needs_order = True

    def polars_expr(self, column: str | None) -> pl.Expr:
        return pl.col(column).drop_nulls().last()

    def sql(self, column: str | None) -> str:
        return f"LAST({column})"


class Count(AggregationPlugin):
    name = "count"
    needs_column = False

    def polars_expr(self, column: str | None) -> pl.Expr:
        return pl.len()

    def sql(self, column: str | None) -> str:
        return "COUNT(*)"


class WhereParams(BaseModel):
    where: str


class Filter(StepPlugin):
    name = "filter"
    Params = WhereParams

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return tools.columns_in(params.where)

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        return lf.filter(ctx.expr(params.where).fill_null(False))


class FormulaParams(BaseModel):
    column: str
    expr: str


class Formula(StepPlugin):
    name = "formula"
    Params = FormulaParams

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return tools.columns_in(params.expr)

    def columns_written(self, params: Any) -> set[str]:
        return {params.column}

    def output_schema(self, params: Any, schema: Any, tools: Any) -> Any:
        return {**schema, params.column: tools.expr_type(params.expr, schema)}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        return ctx.with_column(lf, params.column, params.expr)


class DedupeParams(BaseModel):
    by: list[str]
    depth: int | None = None


class Dedupe(StepPlugin):
    name = "dedupe"
    Params = DedupeParams
    row_local = False

    def lookback(self, params: Any) -> int | None:
        return params.depth

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return set(params.by)

    def key_columns(self, params: Any) -> dict[str, list[str]]:
        return {"data": params.by}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        return lf.sort(["_upload_seq", "_row"]).unique(params.by, keep="last", maintain_order=True)


class LinkParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    with_: str = Field(alias="with")
    on: str


class Link(StepPlugin):
    """Простое левое объединение с другим входом по одному ключу."""

    name = "link"
    Params = LinkParams

    def inputs_used(self, params: Any, tools: Any) -> list[str]:
        return [params.with_]

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return {params.on}

    def key_columns(self, params: Any) -> dict[str, list[str]]:
        return {"data": [params.on], params.with_: [params.on]}

    def output_schema(self, params: Any, schema: Any, tools: Any) -> Any:
        right = tools.input_schema(params.with_)
        if right is None:
            return None
        return {**schema, **{c: t for c, t in right.items() if c not in schema}}

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        names = lf.collect_schema().names()
        right = ctx.input(params.with_)
        cols = [c for c in right.collect_schema().names() if c == params.on or c not in names]
        return lf.join(right.select(cols), on=params.on, how="left")


class QueryParams(BaseModel):
    query: str


class Query(StepPlugin):
    name = "sql"
    Params = QueryParams
    row_local = False

    def inputs_used(self, params: Any, tools: Any) -> list[str]:
        return sorted(tools.tables_in(params.query) - {"data"})

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return tools.query_columns(params.query, "data") or set()

    def reads_all_columns(self, params: Any, tools: Any) -> bool:
        return tools.query_columns(params.query, "data") is None

    def output_schema(self, params: Any, schema: Any, tools: Any) -> Any:
        return tools.query_schema(params.query, {"data": schema})

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        return ctx.sql(params.query, {"data": lf})


class CodeParams(BaseModel):
    code: str
    mode: str = "table"
    frame: str = "pandas"
    timeout: float | None = None
    cache: bool = True


class Code(StepPlugin):
    name = "python"
    Params = CodeParams
    row_local = False

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return set()

    def reads_all_columns(self, params: Any, tools: Any) -> bool:
        return True

    def output_schema(self, params: Any, schema: Any, tools: Any) -> Any:
        return None

    def cacheable(self, params: Any) -> bool:
        return bool(params.cache)

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        return ctx.run_code(lf, params.code, mode=params.mode, frame=params.frame, timeout=params.timeout)


class Boom(StepPlugin):
    """Шаг, который падает при выполнении: так проверяется изоляция ошибок узлов."""

    name = "boom"
    row_local = False

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return set()

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        raise RuntimeError("что-то пошло не так")


class MemoryHistory:
    """История входов в памяти; запоминает, какие границы ей передал движок. Загрузки
    (``uploads``) — только метаданные: строки берутся из ``frames``."""

    def __init__(self, frames: dict[str, pl.DataFrame], schemas: dict[str, InputSchema], coverage=None):
        self.frames = frames
        self.schemas = schemas
        self._coverage = coverage or {}
        self._uploads: dict[str, list[UploadRef]] = {}
        self.calls: list[tuple[str, date | None, date | None]] = []

    def period_column(self, input_id: str) -> str:
        return self.schemas[input_id].period_column

    def columns(self, input_id: str) -> dict[str, DType]:
        return self.schemas[input_id].columns

    def scan(self, input_id, columns=None, lower=None, upper_exclusive=None) -> pl.LazyFrame:
        self.calls.append((input_id, lower, upper_exclusive))
        lf = self.frames[input_id].lazy()
        pc = self.period_column(input_id)
        if lower is not None:
            lf = lf.filter(pl.col(pc) >= lower)
        if upper_exclusive is not None:
            lf = lf.filter(pl.col(pc) < upper_exclusive)
        return lf

    def coverage(self, input_id: str) -> list[DateSpan]:
        return self._coverage.get(input_id, [])

    def uploads(self, input_id: str, span: DateSpan) -> list[UploadRef]:
        return [
            u
            for u in self._uploads.get(input_id, [])
            if (span.start is None or u.period.end_exclusive > span.start) and u.period.start < span.end_exclusive
        ]

    def fingerprint(self, input_id: str) -> str | None:
        df = self.frames[input_id]
        return f"{input_id}-{df.height}-{df.hash_rows().sum()}"


SALES = pl.DataFrame(
    {
        "date": [
            date(2026, 1, 15),
            date(2026, 2, 10),
            date(2026, 2, 20),
            date(2026, 3, 5),
            date(2026, 3, 6),
        ],
        "region": ["A", "A", "B", "A", "B"],
        "amount": [100.0, 200.0, 50.0, 300.0, -10.0],
        "_upload_id": ["u1", "u2", "u2", "u3", "u3"],
        "_upload_seq": pl.Series([1, 2, 2, 3, 3], dtype=pl.Int32),
        "_row": [1, 1, 2, 1, 2],
    }
)
SCHEMA = InputSchema("date", {"date": DType.DATE, "region": DType.STRING, "amount": DType.FLOAT})


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry.from_plugins(
        [
            *[ReportPeriod, Previous, LastYear, LastN, Everything, Sum, Count, Last],
            *[Filter, Formula, Dedupe, Link, Query, Code, Boom],
        ]
    )


@pytest.fixture
def history() -> MemoryHistory:
    return MemoryHistory({"sales": SALES}, {"sales": SCHEMA})
