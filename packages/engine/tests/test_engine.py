from datetime import date
from pathlib import Path

import pytest

from autogenerator.contracts import AgenError, DateSpan, NodeState, Period, ScenarioSpec
from autogenerator.engine import analyze, column_usage, execute

MARCH = Period.parse("2026-03")


def scenario(**kw) -> ScenarioSpec:
    data = {"name": "t", "inputs": [{"id": "sales", "source": "s"}], **kw}
    return ScenarioSpec.model_validate(data)


def run(sc, registry, history, tmp_path: Path, period=MARCH):
    plan = analyze(sc, registry, {"sales": history.schemas["sales"]})
    return execute(plan, registry, history, period, tmp_path)


def test_metrics_datasets_and_formulas(registry, history, tmp_path):
    sc = scenario(
        datasets=[
            {
                "id": "by_month",
                "input": "sales",
                "window": "all",
                "group_by": [{"column": "date", "bucket": "month"}],
                "aggregate": [{"fn": "sum", "column": "amount", "as": "revenue"}],
            },
            {
                "id": "rows",
                "input": "sales",
                "columns": ["region", "amount"],
                "sort": ["-amount"],
                "top": 1,
            },
        ],
        metrics=[
            {"id": "rev", "input": "sales", "fn": "sum", "column": "amount"},
            {
                "id": "prev",
                "input": "sales",
                "window": "previous_period",
                "fn": "sum",
                "column": "amount",
            },
            {"id": "growth", "formula": "rev / prev - 1"},
            {"id": "a_only", "input": "sales", "fn": "count", "where": "region = 'A'"},
            {"id": "zero_div", "formula": "rev / (prev - prev)"},
        ],
    )
    res = run(sc, registry, history, tmp_path)
    assert res.metrics["rev"] == 290.0
    assert res.metrics["prev"] == 250.0
    assert res.metrics["growth"] == pytest.approx(0.16)
    assert res.metrics["a_only"] == 1
    # Деление на ноль — пусто и предупреждение, а не ошибка.
    assert res.metrics["zero_div"] is None
    assert any("zero_div" in i.message for i in res.issues)
    assert res.datasets["by_month"].to_pylist() == [
        {"date": date(2026, 1, 1), "revenue": 100.0},
        {"date": date(2026, 2, 1), "revenue": 250.0},
        {"date": date(2026, 3, 1), "revenue": 290.0},
    ]
    assert res.datasets["rows"].to_pylist() == [{"region": "A", "amount": 300.0}]
    assert (tmp_path / "inputs" / "sales.parquet").exists()


def test_failed_node_blocks_only_its_dependents(registry, history, tmp_path):
    history.frames["plan"] = history.frames["sales"]
    history.schemas["plan"] = history.schemas["sales"]
    sc = ScenarioSpec.model_validate(
        {
            "name": "t",
            "inputs": [
                {"id": "sales", "source": "s", "pipeline": [{"id": "b", "type": "boom"}]},
                {"id": "plan", "source": "s"},
            ],
            "metrics": [
                {"id": "rev", "input": "sales", "fn": "sum", "column": "amount"},
                {"id": "plan", "input": "plan", "fn": "sum", "column": "amount"},
                {"id": "done", "formula": "rev / plan"},
            ],
        }
    )
    plan = analyze(sc, registry, history.schemas)
    res = execute(plan, registry, history, MARCH, tmp_path)
    states = {n.id: (n.state, n.blocked_by) for n in res.nodes}
    assert states["input:sales"][0] == NodeState.ERROR
    assert states["metric:rev"] == (NodeState.SKIPPED, "input:sales")
    assert states["metric:done"] == (NodeState.SKIPPED, "input:sales")
    assert states["metric:plan"] == (NodeState.OK, None)
    assert res.metrics["plan"] == 290.0
    assert "шаг «b» (boom)" in next(n.message for n in res.nodes if n.id == "input:sales")


def test_lower_bound_is_pushed_only_through_row_local_steps(registry, history, tmp_path):
    metrics = [{"id": "rev", "input": "sales", "fn": "sum", "column": "amount"}]
    sc = scenario(metrics=metrics)
    sc.inputs[0].pipeline = []
    run(sc, registry, history, tmp_path)
    assert history.calls[-1] == ("sales", date(2026, 3, 1), date(2026, 4, 1))

    sc = scenario(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [{"id": "f", "type": "filter", "where": "amount > 0"}],
            }
        ],
        metrics=metrics,
    )
    res = run(sc, registry, history, tmp_path)
    assert history.calls[-1][1] == date(2026, 3, 1)
    assert res.metrics["rev"] == 300.0


def test_coverage_gap_warning(registry, history, tmp_path):
    history._coverage["sales"] = [DateSpan(start=date(2026, 3, 1), end_exclusive=date(2026, 4, 1))]
    sc = scenario(metrics=[{"id": "prev", "input": "sales", "window": "previous_period", "fn": "count"}])
    res = run(sc, registry, history, tmp_path)
    assert any("нет загрузок входа «sales» за" in i.message for i in res.issues)


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        (
            {"metrics": [{"id": "m", "input": "sales", "fn": "median", "column": "amount"}]},
            "Нет плагина",
        ),
        ({"metrics": [{"id": "m", "input": "sales", "fn": "sum", "column": "nope"}]}, "nope"),
        ({"metrics": [{"id": "m", "input": "sales", "fn": "sum"}]}, "нужен столбец"),
        ({"metrics": [{"id": "m", "formula": "x + 1"}]}, "нет показателя «x»"),
        ({"metrics": [{"id": "a", "formula": "b"}, {"id": "b", "formula": "a"}]}, "по кругу"),
        (
            {
                "datasets": [
                    {
                        "id": "d",
                        "input": "sales",
                        "group_by": [{"column": "region", "bucket": "month"}],
                        "aggregate": [{"fn": "count"}],
                    }
                ]
            },
            "только столбец дат",
        ),
        (
            {"datasets": [{"id": "d", "input": "sales", "aggregate": [{"fn": "count"}], "sort": ["x"]}]},
            "сортировка",
        ),
        ({"datasets": [{"id": "d", "input": "nope"}]}, "нет входа"),
    ],
)
def test_validation_errors(registry, history, kw, message, tmp_path):
    plan = analyze(scenario(**kw), registry, history.schemas)
    assert any(message in i.message for i in plan.errors), plan.errors
    with pytest.raises(AgenError):
        execute(plan, registry, history, MARCH, tmp_path)


def test_step_errors_are_found_before_reading_data(registry, history):
    sc = scenario(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [{"id": "f", "type": "filter", "where": "x > 0"}],
            }
        ]
    )
    plan = analyze(sc, registry, history.schemas)
    assert [e.node for e in plan.errors] == ["input:sales/step:f"]
    assert history.calls == []


def test_column_usage(registry, history):
    sc = scenario(
        inputs=[
            {
                "id": "sales",
                "source": "s",
                "pipeline": [{"id": "f", "type": "filter", "where": "amount > 0"}],
            }
        ],
        datasets=[{"id": "d", "input": "sales", "group_by": ["region"], "aggregate": [{"fn": "count"}]}],
    )
    usage = column_usage(sc, registry, history.schemas)["sales"]
    assert usage == {
        "date": ["input:sales"],
        "amount": ["input:sales/step:f"],
        "region": ["dataset:d"],
    }
