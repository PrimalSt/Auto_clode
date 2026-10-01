"""Этап M2: типы столбцов при разборе, DuckDB вместо Polars, объединение входов, наборы со
сравнением периодов и расчётами, показатели всех видов, кэш узлов, превью по выборке."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from autogenerator.contracts import DType, NodeState, Period, ScenarioSpec
from autogenerator.engine import EngineOptions, InputSchema, NodeCache, analyze, execute, preview

from .conftest import MemoryHistory

MARCH = Period.parse("2026-03")


def months(*specs: tuple[int, int, str, float]) -> pl.DataFrame:
    """Строки продаж: (год, месяц, регион, сумма) — по загрузке на месяц."""
    seqs = {}
    rows = []
    for y, m, region, amount in specs:
        seq = seqs.setdefault((y, m), len(seqs) + 1)
        rows.append(
            {
                "date": date(y, m, 10),
                "region": region,
                "amount": amount,
                "_upload_id": f"u{seq}",
                "_upload_seq": seq,
                "_row": len(rows) + 1,
            }
        )
    return pl.DataFrame(rows, schema_overrides={"_upload_seq": pl.Int32})


SALES = months(
    (2025, 2, "A", 5.0),
    (2025, 3, "A", 40.0),
    (2025, 3, "B", 10.0),
    (2026, 1, "A", 100.0),
    (2026, 2, "A", 200.0),
    (2026, 2, "B", 50.0),
    (2026, 3, "A", 300.0),
    (2026, 3, "B", 60.0),
    (2026, 3, "C", 40.0),
)
SCHEMA = InputSchema("date", {"date": DType.DATE, "region": DType.STRING, "amount": DType.FLOAT})
PLAN = pl.DataFrame(
    {
        "date": [date(2026, 3, 1), date(2026, 3, 1), date(2026, 2, 1)],
        "region": ["A", "B", "A"],
        "target": [250.0, 80.0, 150.0],
        "_upload_id": ["p1"] * 3,
        "_upload_seq": pl.Series([1, 1, 1], dtype=pl.Int32),
        "_row": [1, 2, 3],
    }
)
PLAN_SCHEMA = InputSchema("date", {"date": DType.DATE, "region": DType.STRING, "target": DType.FLOAT})


@pytest.fixture
def hist() -> MemoryHistory:
    return MemoryHistory({"sales": SALES, "plan": PLAN}, {"sales": SCHEMA, "plan": PLAN_SCHEMA})


def sc(**kw) -> ScenarioSpec:
    data = {"name": "t", "inputs": [{"id": "sales", "source": "s", "main": True}, {"id": "plan", "source": "p"}], **kw}
    return ScenarioSpec.model_validate(data)


def go(spec, registry, hist, tmp_path: Path, period=MARCH, **opts):
    plan = analyze(spec, registry, hist.schemas)
    assert not plan.errors, plan.errors
    return execute(plan, registry, hist, period, tmp_path, EngineOptions(**opts))


# --- разбор с типами ---------------------------------------------------------------


def test_types_flow_through_steps_and_errors_come_from_duckdb(registry, hist):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [
                    {"id": "net", "type": "formula", "column": "net", "expr": "amount / 1.2"},
                    {"id": "month", "type": "formula", "column": "m", "expr": "strftime(date, '%Y-%m')"},
                    {"id": "bad", "type": "formula", "column": "x", "expr": "region + 1"},
                ],
            },
            {"id": "plan", "source": "p"},
        ]
    )
    plan = analyze(spec, registry, hist.schemas)
    errors = {e.node: e.message for e in plan.errors}
    assert list(errors) == ["input:sales/step:bad"]
    assert "region + 1" in errors["input:sales/step:bad"]
    assert plan.inputs["sales"].steps[0].plugin.name == "formula"
    plan.inputs["sales"].steps.pop()  # без ошибочного шага
    ok = analyze(
        sc(inputs=[{**spec.inputs[0].model_dump(), "pipeline": spec.inputs[0].model_dump()["pipeline"][:2]}]),
        registry,
        hist.schemas,
    )
    assert ok.inputs["sales"].schema_after is not None
    assert ok.inputs["sales"].schema_after["net"] == DType.FLOAT
    assert ok.inputs["sales"].schema_after["m"] == DType.STRING


def test_sql_step_reads_other_input_and_reports_columns(registry, hist):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [
                    {
                        "id": "q",
                        "type": "sql",
                        "query": "SELECT d.date, d.region, d.amount, p.target FROM data d "
                        "LEFT JOIN plan p ON p.region = d.region AND p.date = date_trunc('month', d.date)",
                    }
                ],
            },
            {"id": "plan", "source": "p"},
        ]
    )
    plan = analyze(spec, registry, hist.schemas)
    assert not plan.errors, plan.errors
    assert plan.inputs["sales"].deps == ["plan"]
    assert plan.input_order == ["plan", "sales"]
    assert plan.inputs["sales"].schema_after == {
        "date": DType.DATE,
        "region": DType.STRING,
        "amount": DType.FLOAT,
        "target": DType.FLOAT,
    }
    assert set(plan.usage["sales"]) == {"date", "region", "amount"}


def test_input_cycle_is_an_error(registry, hist):
    spec = sc(
        inputs=[
            {"id": "sales", "source": "s", "pipeline": [{"id": "j", "type": "link", "with": "plan", "on": "region"}]},
            {"id": "plan", "source": "p", "pipeline": [{"id": "j", "type": "link", "with": "sales", "on": "region"}]},
        ]
    )
    plan = analyze(spec, registry, hist.schemas)
    assert any("по кругу" in e.message for e in plan.errors)


# --- выполнение --------------------------------------------------------------------


def test_formula_falls_back_to_duckdb(registry, hist, tmp_path):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [{"id": "lev", "type": "formula", "column": "d", "expr": "levenshtein(region, 'AB')"}],
            },
            {"id": "plan", "source": "p"},
        ],
        metrics=[{"id": "d", "input": "sales", "fn": "sum", "column": "d"}],
    )
    res = go(spec, registry, hist, tmp_path)
    assert res.metrics["d"] == 4  # «A» и «B» — одна правка до «AB», «C» — две


def test_join_and_sql_dataset_plan_fact(registry, hist, tmp_path):
    spec = sc(
        datasets=[
            {
                "id": "plan_fact",
                "query": "SELECT s.region, SUM(s.amount) AS fact, MAX(p.target) AS plan, "
                "SUM(s.amount) / MAX(p.target) AS done FROM sales s LEFT JOIN plan p USING (region) "
                "GROUP BY s.region ORDER BY s.region",
            }
        ],
        metrics=[
            {"id": "fact", "query": "SELECT SUM(amount) FROM sales"},
            {"id": "plan", "query": "SELECT SUM(target) FROM plan"},
            {"id": "done", "formula": "fact / plan"},
        ],
    )
    res = go(spec, registry, hist, tmp_path)
    assert res.datasets["plan_fact"].to_pylist() == [
        {"region": "A", "fact": 300.0, "plan": 250.0, "done": 1.2},
        {"region": "B", "fact": 60.0, "plan": 80.0, "done": 0.75},
        {"region": "C", "fact": 40.0, "plan": None, "done": None},
    ]
    assert res.metrics["fact"] == 400.0
    assert res.metrics["plan"] == 330.0
    assert res.metrics["done"] == pytest.approx(400 / 330)


def test_dataset_compare_derive_top_and_others(registry, hist, tmp_path):
    spec = sc(
        datasets=[
            {
                "id": "regions",
                "input": "sales",
                "group_by": ["region"],
                "aggregate": [{"fn": "sum", "column": "amount", "as": "revenue"}],
                "compare": ["previous_period", "same_period_last_year"],
                "derive": [
                    {"fn": "share", "column": "revenue"},
                    {"fn": "cumsum", "column": "revenue"},
                    {"fn": "rank", "column": "revenue"},
                    {"expr": "revenue - revenue_prev", "as": "delta"},
                ],
                "sort": ["-revenue"],
                "top": 1,
                "others": "Прочие",
            }
        ]
    )
    res = go(spec, registry, hist, tmp_path)
    rows = res.datasets["regions"].to_pylist()
    assert rows[0] == {
        "region": "A",
        "revenue": 300.0,
        "revenue_prev": 200.0,
        "revenue_prev_change": 100.0,
        "revenue_prev_change_pct": 0.5,
        "revenue_ly": 40.0,
        "revenue_ly_change": 260.0,
        "revenue_ly_change_pct": 6.5,
        "revenue_share": 0.75,
        "revenue_cumsum": 300.0,
        "revenue_rank": 1,
        "delta": 100.0,
    }
    others = rows[1]
    assert others["region"] == "Прочие"
    assert others["revenue"] == 100.0  # B + C
    assert others["revenue_prev"] == 50.0  # B в феврале
    assert others["revenue_ly"] == 10.0  # B в марте 2025
    assert others["revenue_share"] == 0.25
    assert others["revenue_cumsum"] == 400.0
    assert others["revenue_rank"] is None
    assert others["delta"] == 50.0


def test_compare_of_counts_can_go_down(registry, hist, tmp_path):
    # В апреле 2025 продаж нет, в марте — две: разница −2, а не переполнение беззнакового целого.
    spec = sc(
        datasets=[
            {
                "id": "n",
                "input": "sales",
                "aggregate": [{"fn": "count", "as": "orders"}],
                "compare": ["previous_period"],
            }
        ]
    )
    res = go(spec, registry, hist, tmp_path, period=Period.parse("2025-04"))
    assert res.datasets["n"].to_pylist() == [
        {"orders": 0, "orders_prev": 2, "orders_prev_change": -2, "orders_prev_change_pct": -1.0}
    ]


def test_monthly_dynamics_with_last_year_and_pivot(registry, hist, tmp_path):
    spec = sc(
        datasets=[
            {
                "id": "dyn",
                "input": "sales",
                "window": "last_n(3)",
                "group_by": [{"column": "date", "bucket": "month"}],
                "aggregate": [{"fn": "sum", "column": "amount", "as": "revenue"}],
                "compare": ["same_period_last_year"],
            },
            {
                "id": "pivot",
                "input": "sales",
                "window": "last_n(3)",
                "group_by": ["region", {"column": "date", "bucket": "month"}],
                "aggregate": [{"fn": "sum", "column": "amount", "as": "revenue"}],
                "pivot": "date",
            },
        ]
    )
    res = go(spec, registry, hist, tmp_path)
    dyn = {r["date"]: (r["revenue"], r["revenue_ly"]) for r in res.datasets["dyn"].to_pylist()}
    assert dyn == {
        date(2026, 1, 1): (100.0, None),
        date(2026, 2, 1): (250.0, 5.0),
        date(2026, 3, 1): (400.0, 50.0),
    }
    assert res.datasets["pivot"].column_names == ["region", "2026-01", "2026-02", "2026-03"]
    assert res.datasets["pivot"].to_pylist()[0] == {"region": "A", "2026-01": 100.0, "2026-02": 200.0, "2026-03": 300.0}


def test_metric_compare_and_kinds(registry, hist, tmp_path):
    spec = sc(
        datasets=[
            {
                "id": "by_region",
                "input": "sales",
                "group_by": ["region"],
                "aggregate": [{"fn": "sum", "column": "amount", "as": "rev"}],
            }
        ],
        metrics=[
            {
                "id": "revenue",
                "input": "sales",
                "fn": "sum",
                "column": "amount",
                "compare": ["previous_period", "same_period_last_year"],
            },
            {"id": "count", "input": "sales", "fn": "count", "compare": ["previous_period"]},
            {"id": "avg", "formula": "revenue / count", "compare": ["previous_period"]},
            {"id": "best", "dataset": "by_region", "fn": "sum", "column": "rev", "where": "rev > 100"},
            {"id": "last_sale", "input": "sales", "fn": "last", "column": "amount"},
        ],
    )
    res = go(spec, registry, hist, tmp_path)
    m = res.metrics
    assert (m["revenue"], m["revenue_prev"], m["revenue_ly"]) == (400.0, 250.0, 50.0)
    assert m["revenue_prev_change"] == 150.0
    assert m["revenue_prev_change_pct"] == pytest.approx(0.6)
    assert m["revenue_ly_change_pct"] == pytest.approx(7.0)
    assert (m["avg"], m["avg_prev"]) == (pytest.approx(400 / 3), 125.0)
    assert m["best"] == 300.0
    assert m["last_sale"] == 40.0


def test_python_dataset_and_metric_run_in_a_separate_process(registry, hist, tmp_path):
    spec = sc(
        datasets=[
            {
                "id": "py",
                "inputs": ["sales"],
                "code": "def build(tables, ctx):\n"
                "    df = tables['sales']\n"
                "    print('строк', len(df))\n"
                "    return df.groupby('region', as_index=False)['amount'].sum()\n",
            }
        ],
        metrics=[
            {
                "id": "py_metric",
                "inputs": ["sales", "py"],
                "code": "def value(tables, ctx):\n"
                "    ctx.warn('период ' + str(ctx.period.key))\n"
                "    return tables['py']['amount'].max()\n",
            }
        ],
    )
    res = go(spec, registry, hist, tmp_path)
    assert sorted(res.datasets["py"].to_pylist(), key=lambda r: r["region"])[0] == {"region": "A", "amount": 300.0}
    assert res.metrics["py_metric"] == 300.0
    assert any(i.message == "строк 3" for i in res.issues)
    assert any(i.message == "период 2026-03" for i in res.issues)


def test_user_code_error_shows_line_and_blocks_only_dependents(registry, hist, tmp_path):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [
                    {
                        "id": "py",
                        "type": "python",
                        "code": "def transform(df, ctx):\n    x = 1\n    return df[undefined]\n",
                    }
                ],
            },
            {"id": "plan", "source": "p"},
        ],
        metrics=[
            {"id": "rev", "input": "sales", "fn": "sum", "column": "amount"},
            {"id": "plan", "input": "plan", "fn": "sum", "column": "target"},
        ],
    )
    res = go(spec, registry, hist, tmp_path)
    node = next(n for n in res.nodes if n.id == "input:sales")
    assert node.state == NodeState.ERROR
    assert "строка 3" in (node.message or "") and "NameError" in (node.message or "")
    assert res.metrics["plan"] == 250.0 + 80.0


def test_user_code_timeout(registry, hist, tmp_path):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [
                    {
                        "id": "py",
                        "type": "python",
                        "timeout": 2,
                        "code": "def transform(df, ctx):\n    while True:\n        pass\n",
                    }
                ],
            },
            {"id": "plan", "source": "p"},
        ],
    )
    res = go(spec, registry, hist, tmp_path)
    node = next(n for n in res.nodes if n.id == "input:sales")
    assert node.state == NodeState.ERROR
    assert "дольше 2 с" in (node.message or "")


def test_lazy_code_and_large_data_go_through_duckdb(registry, hist, tmp_path):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [
                    {
                        "id": "py",
                        "type": "python",
                        "mode": "lazy",
                        "code": "import polars as pl\n"
                        "def transform(lf, ctx):\n    return lf.with_columns(x=pl.col('amount') * 2)\n",
                    },
                    {
                        "id": "q",
                        "type": "sql",
                        "query": "SELECT *, row_number() OVER (ORDER BY amount DESC) AS pos FROM data",
                    },
                ],
            },
            {"id": "plan", "source": "p"},
        ],
        metrics=[{"id": "x", "input": "sales", "fn": "sum", "column": "x", "where": "pos = 1"}],
    )
    res = go(spec, registry, hist, tmp_path, large_rows=1)
    assert res.metrics["x"] == 600.0


# --- граница истории, кэш -----------------------------------------------------------


def test_lower_bound_covers_compare_windows_and_lookback(registry, hist, tmp_path):
    metrics = [{"id": "rev", "input": "sales", "fn": "sum", "column": "amount", "compare": ["same_period_last_year"]}]
    go(sc(metrics=metrics), registry, hist, tmp_path)
    assert ("sales", date(2025, 3, 1), date(2026, 4, 1)) in hist.calls
    hist.calls.clear()
    spec = sc(
        inputs=[
            {"id": "sales", "source": "s", "pipeline": [{"id": "d", "type": "dedupe", "by": ["region"], "depth": 2}]},
            {"id": "plan", "source": "p"},
        ],
        metrics=[{"id": "rev", "input": "sales", "fn": "count"}],
    )
    go(spec, registry, hist, tmp_path)
    assert ("sales", date(2026, 1, 1), date(2026, 4, 1)) in hist.calls  # март минус 2 месяца


def test_node_cache_skips_reading_history_again(registry, hist, tmp_path):
    cache = NodeCache(tmp_path / "cache")
    spec = sc(
        datasets=[
            {
                "id": "py",
                "inputs": ["sales"],
                "code": "def build(tables, ctx):\n"
                "    print('строк', len(tables['sales']))\n    return tables['sales']\n",
            }
        ],
        metrics=[{"id": "rev", "input": "sales", "fn": "sum", "column": "amount"}],
    )
    first = go(spec, registry, hist, tmp_path / "w1", cache=cache)
    calls = len(hist.calls)
    second = go(spec, registry, hist, tmp_path / "w2", cache=cache)
    assert first.metrics == second.metrics
    assert len(hist.calls) == calls  # история не читалась
    # Вывод кода и предупреждения о пропусках в загрузках при попадании в кэш — те же.
    logs = [i.message for i in second.issues if i.node == "dataset:py"]
    assert logs == [i.message for i in first.issues if i.node == "dataset:py"]
    assert "строк 3" in logs and any("нет загрузок" in m for m in logs)
    # Превью после шага — своя запись кэша: повторное превью не читает историю.
    plan = analyze(spec, registry, hist.schemas)
    opts = EngineOptions(cache=cache)
    a = preview(plan, registry, hist, MARCH, tmp_path / "p1", "sales", options=opts)
    calls = len(hist.calls)
    b = preview(plan, registry, hist, MARCH, tmp_path / "p2", "sales", options=opts)
    assert len(hist.calls) == calls and a.rows == b.rows and a.steps == b.steps
    # Новая загрузка — другой отпечаток: кэш не подходит.
    hist.frames["sales"] = SALES.filter(pl.col("region") != "C")
    third = go(spec, registry, hist, tmp_path / "w3", cache=cache)
    assert third.metrics["rev"] == 360.0


# --- превью -------------------------------------------------------------------------


def test_step_preview_counts_rows(registry, hist, tmp_path):
    spec = sc(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [
                    {"id": "big", "type": "filter", "where": "amount > 45"},
                    {"id": "net", "type": "formula", "column": "net", "expr": "amount / 2"},
                    {"id": "off", "type": "filter", "where": "amount > 1000", "enabled": False},
                ],
            },
            {"id": "plan", "source": "p"},
        ]
    )
    plan = analyze(spec, registry, hist.schemas)
    res = preview(plan, registry, hist, MARCH, tmp_path, "sales/big", rows=2)
    assert res.target == "input:sales/step:big"
    assert [(s.id, s.rows_before, s.rows_after) for s in res.steps if s.enabled] == [("big", 9, 5)]
    assert [s.id for s in res.steps if not s.enabled] == ["off"]
    assert res.total_rows == 5
    assert len(res.rows) == 2 and [c.name for c in res.columns] == ["date", "region", "amount"]
    full = preview(plan, registry, hist, MARCH, tmp_path, "sales")
    assert [(s.id, s.rows_after) for s in full.steps if s.enabled] == [("big", 5), ("net", 5)]
    assert "net" in [c.name for c in full.columns]


def test_metric_and_dataset_preview(registry, hist, tmp_path):
    spec = sc(
        datasets=[{"id": "d", "input": "sales", "group_by": ["region"], "aggregate": [{"fn": "count", "as": "n"}]}],
        metrics=[{"id": "rev", "input": "sales", "fn": "sum", "column": "amount", "compare": ["previous_period"]}],
    )
    plan = analyze(spec, registry, hist.schemas)
    m = preview(plan, registry, hist, MARCH, tmp_path, "rev")
    assert m.value == 400.0
    assert m.metrics == pytest.approx(
        {"rev": 400.0, "rev_prev": 250.0, "rev_prev_change": 150.0, "rev_prev_change_pct": 0.6}
    )
    d = preview(plan, registry, hist, MARCH, tmp_path, "dataset:d")
    assert d.total_rows == 3 and not d.approximate


def test_sample_keeps_join_pairs_and_duplicates(registry, tmp_path):
    n = 4000
    sales = pl.DataFrame(
        {
            "date": [date(2026, 3, 1 + i % 28) for i in range(n)],
            "client": [f"c{i % 500}" for i in range(n)],
            "amount": [1.0] * n,
            "_upload_id": ["u1"] * n,
            "_upload_seq": pl.Series([1] * n, dtype=pl.Int32),
            "_row": list(range(n)),
        }
    )
    clients = pl.DataFrame(
        {
            "date": [date(2026, 3, 1)] * 500,
            "client": [f"c{i}" for i in range(500)],
            "segment": ["S" if i % 2 else "L" for i in range(500)],
            "_upload_id": ["k1"] * 500,
            "_upload_seq": pl.Series([1] * 500, dtype=pl.Int32),
            "_row": list(range(500)),
        }
    )
    hist = MemoryHistory(
        {"sales": sales, "clients": clients},
        {
            "sales": InputSchema("date", {"date": DType.DATE, "client": DType.STRING, "amount": DType.FLOAT}),
            "clients": InputSchema("date", {"date": DType.DATE, "client": DType.STRING, "segment": DType.STRING}),
        },
    )
    spec = ScenarioSpec.model_validate(
        {
            "name": "t",
            "inputs": [
                {
                    "id": "sales",
                    "source": "s",
                    "pipeline": [{"id": "j", "type": "link", "with": "clients", "on": "client"}],
                },
                {"id": "clients", "source": "c"},
            ],
        }
    )
    plan = analyze(spec, registry, hist.schemas)
    res = preview(
        plan,
        registry,
        hist,
        MARCH,
        tmp_path,
        "sales",
        sample=10,
        options=EngineOptions(cache=NodeCache(tmp_path / "c")),
    )
    assert res.approximate and res.sample is not None
    assert res.sample.keys == {"clients": ["client"], "sales": ["client"]}
    out = pl.DataFrame(res.rows)
    rows = preview(plan, registry, hist, MARCH, tmp_path, "sales", sample=10, rows=10_000)
    got = pl.DataFrame(rows.rows)
    # Все строки выборки нашли пару: справочник взят по тому же хешу ключа.
    assert got["segment"].null_count() == 0
    # Клиент целиком в выборке или целиком вне её: строк у каждого — как в полной истории (8).
    assert set(got.group_by("client").len()["len"].to_list()) == {8}
    assert rows.total_rows == got.height * 10
    assert out.height == 20
