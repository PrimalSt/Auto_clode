"""Шаги, окна и агрегаты проверяются без движка: формулы переводит ``pl.sql_expr``."""

from datetime import date

import polars as pl
import pytest

from autogenerator.contracts import DateSpan, Period
from autogenerator.steps_std.aggregations import CountAgg, CountDistinctAgg, MeanAgg, SumAgg
from autogenerator.steps_std.steps import DedupeStep, FilterStep, FormulaStep, SelectStep
from autogenerator.steps_std.windows import (
    AllWindow,
    LastNWindow,
    PreviousPeriodWindow,
    QuarterToDateWindow,
    RangeWindow,
    ReportPeriodWindow,
    SamePeriodLastYearWindow,
    YearToDateWindow,
)


class Ctx:
    input_id = "sales"
    period = Period.parse("2026-03")
    period_column = "date"

    def expr(self, sql: str) -> pl.Expr:
        return pl.sql_expr(sql)

    def columns_in(self, sql: str) -> set[str]:
        return set(pl.sql_expr(sql).meta.root_names())

    def window(self, spec):  # pragma: no cover - шагам M0 не нужно
        raise NotImplementedError

    def warn(self, message: str) -> None:
        pass


LF = pl.LazyFrame(
    {
        "date": [date(2026, 3, d) for d in (1, 2, 3, 4)],
        "order_no": ["a", "b", "a", "c"],
        "amount": [10.0, None, 30.0, -5.0],
        "_upload_seq": pl.Series([2, 1, 1, 1], dtype=pl.Int32),
        "_row": [1, 1, 2, 3],
    }
)


def run(step, **params):
    p = step.parse_params(params)
    return step.apply(LF, p, Ctx()).collect()


def test_filter_drops_nulls_like_sql():
    assert run(FilterStep(), where="amount > 0")["amount"].to_list() == [10.0, 30.0]
    assert FilterStep().columns_used(FilterStep().parse_params({"where": "amount > 0"}), Ctx()) == {"amount"}


def test_dedupe_keeps_row_from_latest_upload():
    # «a» есть в загрузке 1 (строка 2) и в загрузке 2 (строка 1): остаётся из загрузки 2.
    df = run(DedupeStep(), by=["order_no"], keep="last")
    assert sorted(zip(df["order_no"], df["amount"], strict=True), key=lambda r: r[0]) == [
        ("a", 10.0),
        ("b", None),
        ("c", -5.0),
    ]
    assert not DedupeStep.row_local
    first = run(DedupeStep(), by=["order_no"], keep="first")
    assert first.filter(pl.col("order_no") == "a")["amount"].item() == 30.0


def test_formula_and_select():
    step = FormulaStep()
    p = step.parse_params({"column": "net", "expr": "amount / 2"})
    assert step.output_columns(p, ["amount"]) == ["amount", "net"]
    assert run(step, column="net", expr="amount / 2")["net"].to_list() == [5.0, None, 15.0, -2.5]
    df = run(SelectStep(), columns=["amount"])
    assert df.columns == ["date", "amount", "_upload_seq", "_row"]


def test_params_are_validated():
    with pytest.raises(ValueError):
        DedupeStep().parse_params({"by": []})
    with pytest.raises(ValueError):
        FilterStep().parse_params({"where": "x > 0", "лишний": 1})
    with pytest.raises(ValueError, match="новее"):
        FilterStep().parse_params({"where": "x"}, type_version=2)


MARCH = Period.parse("2026-03")


def span(start: str | None, end: str) -> DateSpan:
    return DateSpan(start=date.fromisoformat(start) if start else None, end_exclusive=date.fromisoformat(end))


@pytest.mark.parametrize(
    ("window", "params", "expected"),
    [
        (ReportPeriodWindow(), {}, span("2026-03-01", "2026-04-01")),
        (PreviousPeriodWindow(), {}, span("2026-02-01", "2026-03-01")),
        (SamePeriodLastYearWindow(), {}, span("2025-03-01", "2025-04-01")),
        (QuarterToDateWindow(), {}, span("2026-01-01", "2026-04-01")),
        (YearToDateWindow(), {}, span("2026-01-01", "2026-04-01")),
        (LastNWindow(), {"n": 6}, span("2025-10-01", "2026-04-01")),
        (AllWindow(), {}, span(None, "2026-04-01")),
        (
            RangeWindow(),
            {"start": "2026-01-10", "end": "2026-01-20"},
            span("2026-01-10", "2026-01-21"),
        ),
    ],
)
def test_windows(window, params, expected):
    assert window.resolve(MARCH, window.parse_params(params)) == expected


def test_quarter_to_date_for_a_range_period():
    p = Period.range(date(2026, 2, 20), date(2026, 4, 10))
    # Квартал берётся по концу периода, но окно не короче самого периода.
    assert QuarterToDateWindow().resolve(p, None) == span("2026-02-20", "2026-04-11")


def test_aggregations():
    df = pl.DataFrame({"x": [1.0, None, 3.0], "k": ["a", "a", None]})
    assert df.select(SumAgg().polars_expr("x")).item() == 4.0
    assert df.filter(pl.lit(False)).select(SumAgg().polars_expr("x")).item() is None
    assert df.select(CountAgg().polars_expr(None)).item() == 3
    assert df.select(CountAgg().polars_expr("x")).item() == 2
    assert df.select(CountDistinctAgg().polars_expr("k")).item() == 1
    assert df.select(MeanAgg().polars_expr("x")).item() == 2.0
    assert SumAgg().sql("x") == "SUM(x)"
