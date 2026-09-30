"""Встроенные шаги обработки (ARCHITECTURE.md, раздел 6.4).

Этап M0: ``filter``, ``dedupe``, ``formula``, ``select``. Остальные шаги MVP (``time_filter``,
``sort``, ``rename``, ``cast``, ``join``, ``sql``, ``python``) — на этапе M2.
"""

from __future__ import annotations

from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from autogenerator.contracts import ExpressionTools, StepContext, StepPlugin


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterParams(_Params):
    where: str = Field(description="Условие на SQL, например amount > 0 AND region <> 'Прочие'")


class FilterStep(StepPlugin):
    name = "filter"
    title = "Фильтр"
    Params = FilterParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return tools.columns_in(params.where)

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        # Пустой результат условия (NULL) строку не пропускает, как в SQL.
        return lf.filter(ctx.expr(params.where).fill_null(False))


class DedupeParams(_Params):
    by: list[str] = Field(min_length=1, description="Столбцы, по которым ищутся дубликаты")
    keep: Literal["first", "last"] = Field(
        "last", description="Какую строку оставить: из более ранней или более поздней загрузки"
    )


class DedupeStep(StepPlugin):
    """Удаление дубликатов по всей истории: «последняя» — из самой новой загрузки, а внутри
    загрузки — ниже по файлу. На больших данных шаг переедет в DuckDB (этап M2)."""

    name = "dedupe"
    title = "Удалить дубликаты"
    Params = DedupeParams
    row_local = False

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.by)

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        names = lf.collect_schema().names()
        order = [c for c in ("_upload_seq", "_row") if c in names]
        if order:
            lf = lf.sort(order)
        return lf.unique(subset=params.by, keep=params.keep, maintain_order=True)


class FormulaParams(_Params):
    column: str = Field(description="id нового или заменяемого столбца")
    expr: str = Field(description="Выражение на SQL, например amount / 1.2")


class FormulaStep(StepPlugin):
    name = "formula"
    title = "Формула"
    Params = FormulaParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return tools.columns_in(params.expr)

    def output_columns(self, params: Any, columns: list[str]) -> list[str]:
        return columns if params.column in columns else [*columns, params.column]

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        return lf.with_columns(ctx.expr(params.expr).alias(params.column))


class SelectParams(_Params):
    columns: list[str] = Field(min_length=1, description="Столбцы, которые остаются")


class SelectStep(StepPlugin):
    """Оставить только перечисленные столбцы. Столбец периода и служебные остаются всегда."""

    name = "select"
    title = "Выбрать столбцы"
    Params = SelectParams
    row_local = True

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.columns)

    def output_columns(self, params: Any, columns: list[str]) -> list[str]:
        return [c for c in columns if c in params.columns or c.startswith("_")]

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        names = lf.collect_schema().names()
        keep = [ctx.period_column, *params.columns, *(c for c in names if c.startswith("_"))]
        return lf.select([c for c in dict.fromkeys(keep) if c in names])
