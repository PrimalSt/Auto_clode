"""``python -m autogenerator.engine сценарий.yaml --data папка`` — расчёты без остального приложения.

В папке ``--data`` лежит история каждого входа: ``<вход>.parquet`` или папка ``<вход>/`` с
Parquet (например, сохранённая ``python -m autogenerator.history ... --out``). Столбцы
должны называться по ``id`` источника. Схемы входов берутся из ``--sources``.
Печатает показатели и первые строки наборов; с ``--out`` сохраняет их в папку. С
``--preview узел`` показывает превью узла: ``sales/dedupe``, ``by_month``, ``revenue``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

from autogenerator.contracts import (
    AgenError,
    DateSpan,
    DType,
    Period,
    SourceSpec,
    UploadRef,
)
from autogenerator.contracts.yaml_io import load_model_list, load_yaml
from autogenerator.plugin_host import PluginRegistry

from .analysis import InputSchema, analyze
from .execute import execute
from .loading import load_scenario
from .preview import preview


class FolderHistory:
    """История из готовых Parquet-файлов: без правил пересечения и без покрытия."""

    def __init__(self, folder: Path, schemas: dict[str, InputSchema]):
        self.folder = folder
        self.schemas = schemas

    def period_column(self, input_id: str) -> str:
        return self.schemas[input_id].period_column

    def columns(self, input_id: str) -> dict[str, DType]:
        return self.schemas[input_id].columns

    def scan(
        self,
        input_id: str,
        columns: list[str] | None = None,
        lower: date | None = None,
        upper_exclusive: date | None = None,
    ) -> pl.LazyFrame:
        f = self.folder / f"{input_id}.parquet"
        lf = pl.scan_parquet(
            f if f.exists() else self.folder / input_id / "**" / "*.parquet",
            hive_partitioning=False,
        )
        pc = self.period_column(input_id)
        if lower is not None:
            lf = lf.filter(pl.col(pc) >= lower)
        if upper_exclusive is not None:
            lf = lf.filter(pl.col(pc) < upper_exclusive)
        return lf

    def coverage(self, input_id: str) -> list[DateSpan]:
        # Какими загрузками собраны файлы, здесь неизвестно: считаем, что данные есть за всё
        # время, и предупреждений о пропусках не выдаём.
        return [DateSpan(start=None, end_exclusive=date.max)]

    def uploads(self, input_id: str, span: DateSpan) -> list[UploadRef]:
        return []  # загрузки неизвестны: проверки столбцов загрузок нет

    def fingerprint(self, input_id: str) -> str | None:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.engine")
    ap.add_argument("scenario")
    ap.add_argument("--data", required=True, help="папка с историей входов")
    ap.add_argument("--sources", help="sources.yaml; по умолчанию — рядом со сценарием")
    ap.add_argument("--period", required=True, help="отчётный период: 2026-03, 2026-Q1, …")
    ap.add_argument("--out", help="папка для наборов (.parquet) и показателей (metrics.json)")
    ap.add_argument("--workdir", default=".agen-engine", help="рабочая папка движка")
    ap.add_argument("--preview", metavar="УЗЕЛ", help="превью узла вместо расчёта всего сценария")
    a = ap.parse_args(argv)
    try:
        sc_path = Path(a.scenario)
        scenario = load_scenario(load_yaml(sc_path), sc_path.name)
        sources = {s.id: s for s in load_model_list(SourceSpec, a.sources or sc_path.parent / "sources.yaml")}
        schemas = {i.id: InputSchema.from_source(sources[i.source]) for i in scenario.inputs if i.source in sources}
        registry = PluginRegistry.discover()
        plan = analyze(scenario, registry, schemas)
        history = FolderHistory(Path(a.data), schemas)
        if a.preview:
            pv = preview(plan, registry, history, Period.parse(a.period), a.workdir, a.preview)
            print(pv.model_dump_json(indent=2))
            return 0 if not pv.errors else 2
        result = execute(plan, registry, history, Period.parse(a.period), a.workdir)
    except AgenError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    for n in result.nodes:
        print(f"  {n.state:<8} {n.id:<30} {n.message or ''}")
    print("Показатели:")
    for k, v in result.metrics.items():
        print(f"  {k} = {v}")
    for k, t in result.datasets.items():
        print(f"Набор {k}:")
        print(pl.from_arrow(t))
    for i in result.issues:
        print(f"  {i.level}: {i}")
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for k, t in result.datasets.items():
            pl.from_arrow(t).write_parquet(out / f"{k}.parquet")  # type: ignore[union-attr]
        (out / "metrics.json").write_text(json.dumps(result.metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Сохранено в {out}")
    return 0 if not result.failed_nodes() else 2


if __name__ == "__main__":
    sys.exit(main())
