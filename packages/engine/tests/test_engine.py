from datetime import date
from pathlib import Path

import pytest

from autogenerator.contracts import AgenError, DateSpan, DType, NodeState, Period, ScenarioSpec, UploadRef
from autogenerator.engine import InputSchema, analyze, column_usage, execute, preview

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


def renamed_region(history) -> None:
    """В мартовской выгрузке «Регион» назван иначе: сверка его не нашла, и в загрузке #2 он пустой."""
    history.schemas["sales"] = InputSchema(
        "date",
        {"date": DType.DATE, "region": DType.STRING, "amount": DType.FLOAT},
        names={"date": "Дата", "region": "Регион", "amount": "Сумма"},
    )
    history._uploads["sales"] = [
        UploadRef(
            id="u1",
            seq=1,
            uri="",
            period=Period.parse("2026-01-01..2026-02-28"),
            original_name="янв-фев.csv",
            file_columns=["amount", "date", "region"],
        ),
        UploadRef(
            id="u2",
            seq=2,
            uri="",
            period=Period.parse("2026-03"),
            original_name="мар.csv",
            file_columns=["amount", "date"],
        ),
    ]


def column_warnings(res) -> list[str]:
    return [i.message for i in res.issues if "в загрузке #" in i.message]


BY_REGION = {"id": "d", "input": "sales", "group_by": ["region"], "aggregate": [{"fn": "count"}]}


def test_column_missing_in_upload(registry, history, tmp_path):
    renamed_region(history)
    rev = {"id": "rev", "input": "sales", "fn": "sum", "column": "amount", "where": "region = 'A'"}
    res = run(scenario(datasets=[BY_REGION], metrics=[rev]), registry, history, tmp_path)
    warnings = [i for i in res.issues if "в загрузке #" in i.message]
    assert [(w.level, w.node) for w in warnings] == [("warning", "input:sales")]
    assert warnings[0].message == (
        "в загрузке #2 «мар.csv» (2026-03) нет столбца «Регион» (region) — в ней он пустой, а сценарий его "
        "использует (dataset:d, metric:rev). Если столбец переименован: добавьте новое название в aliases "
        "источника «s» и загрузите файл заново вместо этой загрузки"
    )
    # Все пропавшие столбцы загрузки — в одном предупреждении, в порядке столбцов источника.
    history._uploads["sales"][1].file_columns = ["date"]
    [w] = column_warnings(run(scenario(datasets=[BY_REGION], metrics=[rev]), registry, history, tmp_path))
    assert "нет столбцов «Регион» (region), «Сумма» (amount) — в ней они пустые" in w
    assert "(dataset:d, metric:rev). Если столбцы переименованы: добавьте новые названия" in w
    history._uploads["sales"][1].file_columns = ["amount", "date"]
    # Сценарий без этого столбца и окно, которое до этой загрузки не доходит, — без предупреждения.
    total = {"id": "total", "input": "sales", "fn": "sum", "column": "amount"}
    assert column_warnings(run(scenario(metrics=[total]), registry, history, tmp_path)) == []
    prev = {**BY_REGION, "window": "previous_period"}
    assert column_warnings(run(scenario(datasets=[prev]), registry, history, tmp_path)) == []
    # Превью предупреждений о данных не пишет.
    plan = analyze(scenario(datasets=[BY_REGION]), registry, history.schemas)
    assert column_warnings(preview(plan, registry, history, MARCH, tmp_path / "p", "dataset:d")) == []


def test_column_missing_in_upload_for_steps(registry, history, tmp_path):
    renamed_region(history)
    # Шаг без нижней границы видит всю прочитанную историю: ему нужен столбец и в мартовской загрузке.
    dedupe = {"id": "dd", "type": "dedupe", "by": ["region"]}
    count = {"id": "n", "input": "sales", "window": "previous_period", "fn": "count"}
    sc = scenario(inputs=[{"id": "sales", "source": "s", "pipeline": [dedupe]}], metrics=[count])
    [w] = column_warnings(run(sc, registry, history, tmp_path))
    assert "«Регион» (region)" in w and "(input:sales/step:dd)" in w
    assert column_warnings(run(sc, registry, history, tmp_path, period=Period.parse("2026-02"))) == []
    # Построчному шагу нужны только строки в окнах: март лежит между окнами за апрель и за апрель
    # прошлого года и в отчёт не попадает. Удалению дубликатов с глубиной 1 нужен и март.
    april = Period.parse("2026-04")
    keep_a = {"id": "f", "type": "filter", "where": "region = 'A'"}
    n = {"id": "n", "input": "sales", "fn": "count"}
    ly = {"id": "ly", "input": "sales", "window": "same_period_last_year", "fn": "count"}
    sc = scenario(inputs=[{"id": "sales", "source": "s", "pipeline": [keep_a]}], metrics=[n, ly])
    assert column_warnings(run(sc, registry, history, tmp_path, period=april)) == []
    sc = scenario(inputs=[{"id": "sales", "source": "s", "pipeline": [{**dedupe, "depth": 1}]}], metrics=[n])
    [w] = column_warnings(run(sc, registry, history, tmp_path, period=april))
    assert w.startswith("в загрузке #2 «мар.csv» (2026-03) нет столбца «Регион» (region)")
    # Формула с id столбца источника заменяет его: дальше это результат шага, столбец файла не нужен.
    formula = {"id": "f", "type": "formula", "column": "region", "expr": "'все'"}
    sc = scenario(inputs=[{"id": "sales", "source": "s", "pipeline": [formula]}], datasets=[BY_REGION])
    assert "region" not in column_usage(sc, registry, history.schemas)["sales"]
    res = run(sc, registry, history, tmp_path)
    assert column_warnings(res) == [] and res.datasets["d"].to_pylist() == [{"region": "все", "count": 2}]


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
