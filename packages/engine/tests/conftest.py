"""Движок проверяется со своими маленькими плагинами и историей в памяти: без steps-std,
history и файлов выгрузок. Ошибка в другом модуле эти тесты не ломает."""

from datetime import date
from typing import Any

import polars as pl
import pytest
from pydantic import BaseModel

from autogenerator.contracts import (
    AggregationPlugin,
    DateSpan,
    DType,
    Period,
    StepPlugin,
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


class Boom(StepPlugin):
    """Шаг, который падает при выполнении: так проверяется изоляция ошибок узлов."""

    name = "boom"
    row_local = False

    def columns_used(self, params: Any, tools: Any) -> set[str]:
        return set()

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: Any) -> pl.LazyFrame:
        raise RuntimeError("что-то пошло не так")


class MemoryHistory:
    """История входов в памяти; запоминает, какие границы ей передал движок."""

    def __init__(self, frames: dict[str, pl.DataFrame], schemas: dict[str, InputSchema], coverage=None):
        self.frames = frames
        self.schemas = schemas
        self._coverage = coverage or {}
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
    return PluginRegistry.from_plugins([ReportPeriod, Previous, Everything, Sum, Count, Filter, Boom])


@pytest.fixture
def history() -> MemoryHistory:
    return MemoryHistory({"sales": SALES}, {"sales": SCHEMA})
