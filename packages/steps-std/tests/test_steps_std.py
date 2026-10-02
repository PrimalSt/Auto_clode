"""Шаги, окна и агрегаты проверяются без движка: формулы переводит ``pl.sql_expr``, запросы
считает DuckDB напрямую, а код на Python выполняется в этом же процессе."""

from datetime import date
from typing import Any

import duckdb
import polars as pl
import pytest

from autogenerator.contracts import DateSpan, DType, Period
from autogenerator.steps_std.aggregations import (
    CountAgg,
    CountDistinctAgg,
    FirstAgg,
    LastAgg,
    MeanAgg,
    MedianAgg,
    SumAgg,
)
from autogenerator.steps_std.code import PythonStep, SqlStep
from autogenerator.steps_std.join import JoinStep
from autogenerator.steps_std.steps import (
    CastStep,
    DedupeStep,
    FilterStep,
    FormulaStep,
    RenameStep,
    SelectStep,
    SortStep,
    TimeFilterStep,
)
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
    """Контекст шага для тестов: всё, что шаги берут у движка, сделано по-простому."""

    input_id = "sales"
    step_id = "s"
    period = Period.parse("2026-03")
    period_column = "date"
    anchor = date(2026, 3, 31)

    def __init__(self, large: bool = False, preview: bool = False, inputs: dict[str, pl.DataFrame] | None = None):
        self.large = large
        self.preview = preview
        self.inputs = inputs or {}
        self.warnings: list[str] = []
        self.queries: list[str] = []

    def expr(self, sql: str) -> pl.Expr:
        return pl.sql_expr(sql)

    def columns_in(self, sql: str) -> set[str]:
        return set(pl.sql_expr(sql).meta.root_names())

    def tables_in(self, sql: str) -> set[str]:
        import sqlglot
        from sqlglot import exp

        return {t.name for t in sqlglot.parse_one(sql, dialect="duckdb").find_all(exp.Table)}

    def query_columns(self, sql: str, table: str) -> set[str] | None:
        return None

    def window(self, spec: Any) -> DateSpan:  # pragma: no cover - шагам не нужно
        raise NotImplementedError

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def log(self, text: str) -> None:
        pass

    def with_column(self, lf: pl.LazyFrame, name: str, sql: str) -> pl.LazyFrame:
        return lf.with_columns(pl.sql_expr(sql).alias(name))

    def filter(self, lf: pl.LazyFrame, sql: str) -> pl.LazyFrame:
        return lf.filter(pl.sql_expr(sql).fill_null(False))

    def input(self, input_id: str) -> pl.LazyFrame:
        return self.inputs[input_id].lazy()

    def sql(self, query: str, tables: dict[str, pl.LazyFrame]) -> pl.LazyFrame:
        self.queries.append(query)
        con = duckdb.connect()
        for name, df in {**self.inputs, **{k: v.collect() for k, v in tables.items()}}.items():
            con.register(name, df.to_arrow())
        return pl.from_arrow(con.execute(query).to_arrow_table()).lazy()  # type: ignore[union-attr]

    def run_code(self, lf: pl.LazyFrame, code: str, **kw: Any) -> pl.LazyFrame:
        ns: dict[str, Any] = {}
        exec(code, ns)
        data = lf if kw.get("mode") == "lazy" else lf.collect().to_pandas()
        out = ns["transform"](data, self)
        return out if isinstance(out, pl.LazyFrame) else pl.from_pandas(out).lazy()


LF = pl.LazyFrame(
    {
        "date": [date(2026, 3, d) for d in (1, 2, 3, 4)],
        "order_no": ["a", "b", "a", "c"],
        "amount": [10.0, None, 30.0, -5.0],
        "_upload_seq": pl.Series([2, 1, 1, 1], dtype=pl.Int32),
        "_row": [1, 1, 2, 3],
    }
)


def run(step, ctx: Ctx | None = None, lf: pl.LazyFrame = LF, **params):
    p = step.parse_params(params)
    return step.apply(lf, p, ctx or Ctx()).collect()


def test_filter_drops_nulls_like_sql():
    assert run(FilterStep(), where="amount > 0")["amount"].to_list() == [10.0, 30.0]
    assert FilterStep().columns_used(FilterStep().parse_params({"where": "amount > 0"}), Ctx()) == {"amount"}


@pytest.mark.parametrize(
    ("conditions", "combine", "expected"),
    [
        ([{"column": "order_no", "op": "eq", "value": "a"}], "and", [10.0, 30.0]),
        ([{"column": "order_no", "op": "in", "value": ["b", "c"]}], "and", [None, -5.0]),
        ([{"column": "order_no", "op": "not_in", "value": ["b", "c"]}], "and", [10.0, 30.0]),
        ([{"column": "amount", "op": "between", "value": [0, 20]}], "and", [10.0]),
        ([{"column": "amount", "op": "is_null"}], "and", [None]),
        ([{"column": "date", "op": "ge", "value": "2026-03-03"}], "and", [30.0, -5.0]),
        (
            [{"column": "order_no", "op": "eq", "value": "c"}, {"column": "amount", "op": "gt", "value": 20}],
            "or",
            [30.0, -5.0],
        ),
        ([{"column": "order_no", "op": "starts_with", "value": "b"}], "and", [None]),
    ],
)
def test_filter_conditions(conditions, combine, expected):
    assert run(FilterStep(), conditions=conditions, combine=combine)["amount"].to_list() == expected


def test_filter_warns_about_new_upload():
    ctx = Ctx()
    run(FilterStep(), ctx, conditions=[{"column": "order_no", "op": "in", "value": ["b", "c"]}])
    # В новой загрузке (2) только «a»: условие не нашло строк, а «a» — значение, которого раньше
    # не было бы, если бы… оно было и раньше, поэтому новых значений нет.
    assert ctx.warnings == ["в новой выгрузке нет ни одной строки с условием «order_no в списке ['b', 'c']»"]
    lf = LF.with_columns(
        pl.when(pl.col("_upload_seq") == 2).then(pl.lit("z")).otherwise(pl.col("order_no")).alias("order_no")
    )
    ctx = Ctx()
    run(FilterStep(), ctx, lf=lf, conditions=[{"column": "order_no", "op": "in", "value": ["a", "b"]}])
    assert any("появились значения" in w and "z" in w for w in ctx.warnings)
    ctx = Ctx(preview=True)
    run(FilterStep(), ctx, lf=lf, conditions=[{"column": "order_no", "op": "in", "value": ["a", "b"]}])
    assert ctx.warnings == []


def test_time_filter():
    assert run(TimeFilterStep(), start="2026-03-02", end="2026-03-03")["amount"].to_list() == [None, 30.0]
    p = TimeFilterStep().parse_params({"last": 2, "unit": "month"})
    assert p.span(date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 4, 1))
    p = TimeFilterStep().parse_params({"last": 7, "unit": "day"})
    assert p.span(date(2026, 3, 31)) == (date(2026, 3, 25), date(2026, 4, 1))
    p = TimeFilterStep().parse_params({"period": "previous_quarter"})
    assert p.span(date(2026, 3, 15)) == (date(2025, 10, 1), date(2026, 1, 1))
    p = TimeFilterStep().parse_params({"period": "year_to_date"})
    assert p.span(date(2026, 3, 15)) == (date(2026, 1, 1), date(2026, 3, 16))
    with pytest.raises(ValueError):
        TimeFilterStep().parse_params({"last": 2, "period": "current_month"})


def test_dedupe_keeps_row_from_latest_upload():
    # «a» есть в загрузке 1 (строка 2) и в загрузке 2 (строка 1): остаётся из загрузки 2.
    for ctx in (Ctx(), Ctx(large=True)):
        df = run(DedupeStep(), ctx, by=["order_no"], keep="last")
        assert sorted(zip(df["order_no"], df["amount"], strict=True), key=lambda r: r[0]) == [
            ("a", 10.0),
            ("b", None),
            ("c", -5.0),
        ]
    assert "GROUP BY" in ctx.queries[0]
    assert not DedupeStep.row_local
    for ctx in (Ctx(), Ctx(large=True)):
        first = run(DedupeStep(), ctx, by=["order_no"], keep="first")
        assert first.filter(pl.col("order_no") == "a")["amount"].item() == 30.0
        # Порядок строк не меняется.
        assert first["order_no"].to_list() == ["b", "a", "c"]
        top = run(DedupeStep(), ctx, by=["order_no"], keep="min", column="amount")
        assert top.filter(pl.col("order_no") == "a")["amount"].item() == 10.0
    assert "QUALIFY row_number()" in ctx.queries[-1]
    step = DedupeStep()
    assert step.lookback(step.parse_params({"by": ["order_no"], "depth": 3})) == 3
    assert step.lookback(step.parse_params({"by": ["order_no"]})) is None
    assert step.key_columns(step.parse_params({})) == {"data": ["*"]}


def test_sort_select_rename_cast():
    assert run(SortStep(), by=["-amount"])["amount"].to_list() == [30.0, 10.0, -5.0, None]
    assert run(SortStep(), Ctx(large=True), by=["-amount"])["amount"].to_list() == [30.0, 10.0, -5.0, None]
    df = run(SelectStep(), columns=["amount"])
    assert df.columns == ["date", "amount", "_upload_seq", "_row"]
    assert run(SelectStep(), drop=["order_no"]).columns == ["date", "amount", "_upload_seq", "_row"]
    assert run(RenameStep(), columns={"order_no": "order"}).columns[1] == "order"
    with pytest.raises(Exception, match="переименовать нельзя"):
        run(RenameStep(), columns={"date": "d"})
    text = pl.LazyFrame(
        {
            "date": [date(2026, 3, 1)] * 4,
            "x": ["1 234,5", "12", "abc", None],
            "d": ["01.03.2026", "2026-03-02", "x", None],
        }
    )
    ctx = Ctx()
    out = run(CastStep(), ctx, lf=text, columns={"x": "float", "d": "date"})
    assert out["x"].to_list() == [1234.5, 12.0, None, None]
    assert out["d"].to_list() == [date(2026, 3, 1), date(2026, 3, 2), None, None]
    assert ctx.warnings == [
        "«x»: 1 значений не распознаны как float и стали пустыми",
        "«d»: 1 значений не распознаны как date и стали пустыми",
    ]
    ints = run(
        CastStep(), lf=pl.LazyFrame({"date": [date(2026, 3, 1)] * 3, "f": [2.5, 3.5, -2.5]}), columns={"f": "int"}
    )
    assert ints["f"].to_list() == [2, 4, -2]  # к чётному, как CAST в DuckDB


def test_formula_and_schema():
    step = FormulaStep()
    assert run(step, column="net", expr="amount / 2")["net"].to_list() == [5.0, None, 15.0, -2.5]

    class Tools:
        def expr_type(self, sql, schema):
            return DType.FLOAT

    p = step.parse_params({"column": "net", "expr": "amount / 2"})
    assert step.output_schema(p, {"amount": DType.FLOAT}, Tools()) == {"amount": DType.FLOAT, "net": DType.FLOAT}


PLAN = pl.DataFrame({"no": ["a", "b", "b", "z"], "target": [1.0, 2.0, 3.0, 4.0], "_row": [1, 2, 3, 4]})


@pytest.mark.parametrize("large", [False, True])
def test_join(large):
    ctx = Ctx(large=large, inputs={"plan": PLAN})
    df = run(JoinStep(), ctx, **{"with": "plan", "on": {"order_no": "no"}})
    assert sorted(df.select("order_no", "target").rows(), key=lambda r: (r[0], r[1] or 0)) == [
        ("a", 1.0),
        ("a", 1.0),
        ("b", 2.0),
        ("b", 3.0),
        ("c", None),
    ]
    assert "_row" in df.columns and "_row_plan" not in df.columns
    assert ctx.warnings == [
        "во входе «plan» у 1 ключей несколько строк: строки при объединении размножатся",
        "1 строк без пары во входе «plan»: столбцы второго входа у них пустые",
    ]
    anti = run(
        JoinStep(), Ctx(large=large, inputs={"plan": PLAN}), **{"with": "plan", "on": {"order_no": "no"}, "how": "anti"}
    )
    assert anti["order_no"].to_list() == ["c"]
    full = run(
        JoinStep(), Ctx(large=large, inputs={"plan": PLAN}), **{"with": "plan", "on": {"order_no": "no"}, "how": "full"}
    )
    assert "z" in full["order_no"].to_list()


def test_join_schema_and_keys():
    step = JoinStep()
    p = step.parse_params({"with": "plan", "on": ["region"], "columns": ["target"]})
    assert step.key_columns(p) == {"data": ["region"], "plan": ["region"]}

    class Tools:
        def input_schema(self, input_id):
            return {"region": DType.STRING, "target": DType.FLOAT, "date": DType.DATE}

    out = step.output_schema(p, {"region": DType.STRING, "date": DType.DATE}, Tools())
    assert out == {"region": DType.STRING, "date": DType.DATE, "target": DType.FLOAT}
    p = step.parse_params({"with": "plan", "on": ["region"]})
    assert "date_plan" in step.output_schema(p, {"region": DType.STRING, "date": DType.DATE}, Tools())


def test_sql_and_python_steps():
    ctx = Ctx(inputs={"plan": PLAN})
    df = run(SqlStep(), ctx, query="SELECT order_no, SUM(amount) AS s FROM data GROUP BY 1 ORDER BY 1")
    assert df.rows() == [("a", 40.0), ("b", None), ("c", -5.0)]
    assert SqlStep().inputs_used(SqlStep().parse_params({"query": "SELECT * FROM data JOIN plan USING (no)"}), ctx) == [
        "plan"
    ]
    code = "def transform(df, ctx):\n    df['x'] = df['amount'] * 2\n    return df\n"
    assert run(PythonStep(), code=code)["x"].to_list()[:1] == [20.0]
    step = PythonStep()
    bad = step.parse_params({"code": "def transform(df):\n    return df"})
    assert "2 аргумента" in step.check(bad, ctx)[0].message
    batches = step.parse_params({"code": "def transform(df, ctx):\n    return df.drop_duplicates()", "mode": "batches"})
    assert "строка 2" in step.check(batches, ctx)[0].message
    assert step.lookback(batches) == 0
    assert step.columns_mentioned(
        step.parse_params({"code": "def transform(df, ctx):\n    return df[['amount']]"})
    ) == {"amount"}
    syntax = step.parse_params({"code": "def transform(df, ctx)\n    return df"})
    assert "синтаксическая ошибка в строке 1" in step.check(syntax, ctx)[0].message


def test_params_are_validated():
    with pytest.raises(ValueError):
        DedupeStep().parse_params({"by": []})
    with pytest.raises(ValueError, match="column"):
        DedupeStep().parse_params({"keep": "max"})
    with pytest.raises(ValueError, match="where"):
        FilterStep().parse_params({})
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
    assert df.select(MedianAgg().polars_expr("x")).item() == 2.0
    assert df.select(FirstAgg().polars_expr("x")).item() == 1.0
    assert df.select(LastAgg().polars_expr("x")).item() == 3.0
    assert FirstAgg.needs_order and not SumAgg.needs_order
