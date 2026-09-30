import pytest

from autogenerator.contracts import MetricSpec, ScenarioSpec, SourceSpec, WindowSpec


def test_window_short_forms():
    assert WindowSpec.model_validate("quarter_to_date").type == "quarter_to_date"
    w = WindowSpec.model_validate("last_n(6)")
    assert (w.type, w.params) == ("last_n", {"n": 6})
    w = WindowSpec.model_validate({"type": "range", "start": "2026-01-01", "end": "2026-03-31"})
    assert w.params == {"start": "2026-01-01", "end": "2026-03-31"}


def test_source_period_column_must_be_a_date():
    with pytest.raises(ValueError, match="date или datetime"):
        SourceSpec(
            id="s",
            name="S",
            period_column="x",
            columns=[{"id": "x", "name": "X", "dtype": "string"}],
        )


def test_source_column_ids_are_latin():
    with pytest.raises(ValueError, match="латинских"):
        SourceSpec(
            id="s",
            name="S",
            period_column="d",
            columns=[{"id": "Дата", "name": "Дата", "dtype": "date"}],
        )


def test_metric_needs_formula_or_input():
    with pytest.raises(ValueError, match="formula или пара"):
        MetricSpec(id="m")


def test_scenario_ids_and_main_input():
    sc = ScenarioSpec(
        name="t",
        inputs=[{"id": "sales", "source": "a"}, {"id": "plan", "source": "b", "main": True}],
        metrics=[{"id": "plan", "input": "plan", "fn": "sum", "column": "x"}],
    )
    # У показателей своё пространство имён: вход plan и показатель plan допустимы.
    assert sc.main_input.id == "plan"
    with pytest.raises(ValueError, match="повторяется"):
        ScenarioSpec(name="t", inputs=[{"id": "a", "source": "s"}], datasets=[{"id": "a", "input": "a"}])


def test_step_params_are_kept_as_extra():
    sc = ScenarioSpec(
        name="t",
        inputs=[
            {
                "id": "a",
                "source": "s",
                "pipeline": [{"id": "f", "type": "filter", "where": "x > 0"}],
            }
        ],
    )
    assert sc.inputs[0].pipeline[0].params == {"where": "x > 0"}
