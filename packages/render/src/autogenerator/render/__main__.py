"""``python -m autogenerator.render сценарий.yaml --theme манифест.json --data папка -o отчёт.pptx``
— сборка .pptx без остального приложения.

Манифест шаблона пишет ``python -m autogenerator.theme шаблон.pptx --out манифест.json``,
а наборы и показатели — ``python -m autogenerator.engine … --out папка`` (``<набор>.parquet`` и
``metrics.json``). ``agen run --workdir папка`` сохраняет всё это сам.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

from autogenerator.contracts import AgenError, EngineResult, Period, ScenarioSpec, ThemeManifest
from autogenerator.contracts.yaml_io import load_model
from autogenerator.plugin_host import PluginRegistry

from .build import build_presentation


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.render")
    ap.add_argument("scenario")
    ap.add_argument("--theme", required=True, help="манифест шаблона (JSON)")
    ap.add_argument("--data", required=True, help="папка с <набор>.parquet и metrics.json")
    ap.add_argument("--period", required=True, help="отчётный период: 2026-03, 2026-Q1, …")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    data = Path(a.data)
    try:
        scenario = load_model(ScenarioSpec, a.scenario)
        theme = ThemeManifest.model_validate_json(Path(a.theme).read_text(encoding="utf-8"))
        metrics_file = data / "metrics.json"
        metrics = json.loads(metrics_file.read_text(encoding="utf-8")) if metrics_file.exists() else {}
        datasets = {f.stem: pq.read_table(f) for f in sorted(data.glob("*.parquet"))}
        engine = EngineResult(period=Period.parse(a.period), datasets=datasets, metrics=metrics)
        res = build_presentation(scenario, theme, engine, PluginRegistry.discover(), a.output)
    except AgenError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    for i in res.issues:
        print(f"  {i.level}: {i}")
    if res.output_path:
        print(f"Собрано слайдов: {res.slides} → {res.output_path}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
