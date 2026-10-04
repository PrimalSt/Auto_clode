"""Схема сценария для редактора: принимает то, что принимает проверка сценария (короткие
записи), и знает параметры плагинов."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from autogenerator.contracts import ScenarioSpec
from autogenerator.contracts.yaml_io import loads_yaml
from autogenerator.server.editor_schema import scenario_editor_schema

ROOT = Path(__file__).resolve().parents[3]

SHORT = """
spec_version: 1
name: Короткие записи
inputs:
  - id: sales
    source: sales_crm
    main: true
    pipeline:
      - { id: d, type: dedupe, by: [order_no], keep: last }
      - { id: s, type: sort, by: [-amount, date] }
datasets:
  - id: a
    input: sales
    window: quarter_to_date
    group_by: [region, { column: date, bucket: month }]
    aggregate: [{ column: amount, fn: sum, as: revenue }]
    compare: [previous_period]
    sort: [-revenue]
  - id: b
    input: sales
    window: last_n(6)
    columns: [region]
  - id: c
    input: sales
    window: { type: range, start: 2026-01-01, end: 2026-03-31 }
    columns: [region]
metrics:
  - { id: revenue, input: sales, fn: sum, column: amount, window: year_to_date }
markers:
  Год: period.year
slides:
  - example: 256
    markers:
      Месяц: period.Month
      Выручка: { metric: revenue, scale: млн, decimals: 1, color: sign }
      Число: 5
      Цвет: { text: "{{ metrics.revenue }}", color: 1F4E79 }
    blocks:
      - { type: chart_fill, shape: 6, dataset: a, categories: region, series: [revenue] }
      - type: chart_fill
        shape: { id: 7, label: График }
        dataset: a
        categories: region
        series_from: { column: region, value: revenue, order: [2026, "Прочие"], scale: тыс. }
      - { type: table_fill, shape: 8, dataset: a, columns: [region, { column: revenue, scale: million }] }
  - layout: title_and_content
    blocks:
      - { type: chart, slot: body, dataset: a, x: region, series: [revenue] }
      - { type: table, slot: body, dataset: a, columns: [region, { column: revenue, header: Выручка }] }
"""


@pytest.fixture(scope="module")
def schema() -> dict:
    from autogenerator import worker  # манифест плагинов — в процессе теста, как у исполнителя мелких вызовов

    return scenario_editor_schema(worker.plugin_manifest(False))


def _errors(schema: dict, doc: object) -> list[str]:
    # Даты без кавычек загрузчик YAML читает датами, а редактор (YAML 1.2) — текстом.
    doc = json.loads(json.dumps(doc, default=str))
    v = jsonschema.Draft202012Validator(schema)
    return [f"{list(e.absolute_path)}: {e.message}" for e in v.iter_errors(doc)]


def test_schema_accepts_examples_and_short_forms(schema: dict) -> None:
    assert "x-short" not in json.dumps(schema)
    example = loads_yaml((ROOT / "examples" / "sales" / "scenario.yaml").read_text(encoding="utf-8"))
    assert _errors(schema, example) == []
    short = loads_yaml(SHORT)
    ScenarioSpec.model_validate(short)  # то же принимает и проверка сценария
    assert _errors(schema, short) == []
    # без манифеста (исполнитель мелких вызовов не запустился) — схема без параметров плагинов
    assert _errors(scenario_editor_schema(None), example) == []


def test_schema_knows_plugin_params(schema: dict) -> None:
    doc = loads_yaml(SHORT)
    doc["inputs"][0]["pipeline"][0]["keepp"] = "last"
    doc["datasets"][1]["window"] = {"type": "last_n"}
    doc["slides"][0]["markers"]["Выручка"]["decimal"] = 1
    errors = _errors(schema, doc)
    assert len(errors) == 3, errors
    assert any("keepp" in e for e in errors)
    assert "dedupe" in schema["$defs"]["StepSpec"]["properties"]["type"]["anyOf"][0]["enum"]


def test_broken_params_schema_leaves_plugin_open() -> None:
    from autogenerator import worker

    manifest = worker.plugin_manifest(False)
    for p in manifest.plugins:
        if p.name in ("dedupe", "markers"):
            p.params_schema = {"error": "схема не построилась"}
    schema = scenario_editor_schema(manifest)
    doc = loads_yaml(SHORT)
    doc["inputs"][0]["pipeline"][0]["keepp"] = "last"  # параметры dedupe не проверяются
    assert _errors(schema, doc) == []
    assert "dedupe" in schema["$defs"]["StepSpec"]["properties"]["type"]["anyOf"][0]["enum"]
