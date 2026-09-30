"""Встроенные агрегаты для наборов данных и показателей: выражение Polars и то же на SQL."""

from __future__ import annotations

import polars as pl

from autogenerator.contracts import AggregationPlugin


def _col(column: str | None) -> pl.Expr:
    if column is None:
        raise ValueError("Агрегату нужен столбец")
    return pl.col(column)


class SumAgg(AggregationPlugin):
    name = "sum"
    title = "Сумма"

    def polars_expr(self, column: str | None) -> pl.Expr:
        # Сумма пустого множества — пусто, а не 0: «нет данных» честнее нуля.
        c = _col(column)
        return pl.when(c.count() > 0).then(c.sum()).otherwise(None)

    def sql(self, column: str | None) -> str:
        return f"SUM({column})"


class CountAgg(AggregationPlugin):
    """Число строк; со столбцом — число непустых значений."""

    name = "count"
    title = "Количество"
    needs_column = False

    def polars_expr(self, column: str | None) -> pl.Expr:
        return pl.len() if column is None else pl.col(column).count()

    def sql(self, column: str | None) -> str:
        return "COUNT(*)" if column is None else f"COUNT({column})"


class CountDistinctAgg(AggregationPlugin):
    name = "count_distinct"
    title = "Количество уникальных"

    def polars_expr(self, column: str | None) -> pl.Expr:
        return _col(column).drop_nulls().n_unique()

    def sql(self, column: str | None) -> str:
        return f"COUNT(DISTINCT {column})"


class MeanAgg(AggregationPlugin):
    name = "mean"
    title = "Среднее"

    def polars_expr(self, column: str | None) -> pl.Expr:
        return _col(column).mean()

    def sql(self, column: str | None) -> str:
        return f"AVG({column})"


class MinAgg(AggregationPlugin):
    name = "min"
    title = "Минимум"

    def polars_expr(self, column: str | None) -> pl.Expr:
        return _col(column).min()

    def sql(self, column: str | None) -> str:
        return f"MIN({column})"


class MaxAgg(AggregationPlugin):
    name = "max"
    title = "Максимум"

    def polars_expr(self, column: str | None) -> pl.Expr:
        return _col(column).max()

    def sql(self, column: str | None) -> str:
        return f"MAX({column})"
