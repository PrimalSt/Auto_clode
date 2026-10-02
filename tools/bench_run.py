"""Бенчмарк запуска (PRD, раздел 9; ARCHITECTURE.md, разделы 6.5, 7 и 13).

Цель для компьютера пользователя: отчёт по истории до 50 млн строк (используется до 10
столбцов, дубликаты ищутся по всей истории) собирается не дольше 2 минут. История — та же,
что у бенчмарка превью: 5 загрузок по 10 млн строк месячных продаж с повторами номеров
заказов; отчёт использует 7 столбцов. Отчёт — в синтетическом шаблоне
examples/templates/synthetic.pptx: обложка, итоги с метками и комбинированным графиком,
регионы с круговой диаграммой, таблица регионов по месяцам и слайды из макетов с графиком
и таблицей.

Запуск идёт через то же задание исполнителя ``run``, что и ``agen run``, без кэша узлов
(холодный запуск) и второй раз с кэшем (пересборка после правки слайда).

Примеры::

    uv run python tools/bench_run.py                            # 5 × 10 млн строк
    uv run python tools/bench_run.py --rows 2000000             # 5 × 2 млн
    uv run python tools/bench_run.py --check --report bench.md  # код 1, если цель не достигнута
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from bench_ingest import peak_memory_bytes  # noqa: E402
from bench_preview import ensure_history, manifests, num, sources  # noqa: E402

RUN_SECONDS = 120.0
TEMPLATE = TOOLS.parent / "examples" / "templates" / "synthetic.pptx"


def scenario() -> Any:
    from autogenerator.contracts import ScenarioSpec

    return ScenarioSpec.model_validate(scenario_dict())


def scenario_dict() -> dict[str, Any]:
    return {
        "name": "Бенчмарк запуска",
        "inputs": [
            {
                "id": "sales",
                "source": "sales",
                "main": True,
                "pipeline": [
                    {"id": "dedupe", "type": "dedupe", "by": ["order_no"], "keep": "last"},
                    {"id": "positive", "type": "filter", "where": "amount > 0"},
                    {"id": "net", "type": "formula", "column": "net", "expr": "amount / 1.2"},
                ],
            },
        ],
        "datasets": [
            {
                "id": "month_mix",
                "window": "last_n(3)",
                "query": (
                    "SELECT date_trunc('month', date) AS month, "
                    "sum(CASE WHEN channel = 'Сайт' THEN net ELSE 0 END) AS online, "
                    "sum(CASE WHEN channel <> 'Сайт' THEN net ELSE 0 END) AS offline, "
                    "sum(CASE WHEN channel = 'Сайт' THEN net ELSE 0 END) / sum(net) AS online_share "
                    "FROM sales GROUP BY 1 ORDER BY 1"
                ),
            },
            {
                "id": "by_region",
                "input": "sales",
                "window": "report_period",
                "group_by": ["region"],
                "aggregate": [{"column": "net", "fn": "sum", "as": "revenue"}],
                "sort": ["-revenue"],
            },
            {
                "id": "region_months",
                "input": "sales",
                "window": "year_to_date",
                "group_by": ["region", {"column": "date", "bucket": "month"}],
                "aggregate": [{"column": "net", "fn": "sum", "as": "revenue"}],
                "sort": ["region"],
                "pivot": "date",
            },
            {
                "id": "top_managers",
                "input": "sales",
                "window": "report_period",
                "group_by": ["manager"],
                "aggregate": [{"column": "net", "fn": "sum", "as": "revenue"}, {"fn": "count", "as": "orders"}],
                "sort": ["-revenue"],
                "top": 10,
            },
        ],
        "metrics": [
            {
                "id": "revenue",
                "input": "sales",
                "window": "report_period",
                "column": "net",
                "fn": "sum",
                "compare": ["previous_period"],
            },
            {"id": "margin", "input": "sales", "window": "report_period", "column": "cost", "fn": "sum"},
            {"id": "margin_share", "formula": "1 - margin / revenue"},
            {
                "id": "top_share",
                "window": "report_period",
                "query": "SELECT max(r) / sum(r) FROM (SELECT sum(net) AS r FROM sales GROUP BY region)",
            },
        ],
        "markers": {"Месяц": "period.month", "Год": "period.year"},
        "slides": [
            {"example": 256, "markers": {"Месяц": "period.Month"}},
            {
                "example": 257,
                "markers": {
                    "Выручка": {"metric": "revenue", "scale": "million", "decimals": 1},
                    "Прирост+": {"metric": "revenue_prev_change_pct", "percent": True, "sign": True},
                    "Доля": {"metric": "margin_share", "percent": True, "decimals": 0},
                },
                "blocks": [
                    {
                        "type": "chart_fill",
                        "shape": 6,
                        "dataset": "month_mix",
                        "categories": "month",
                        "series": [
                            {"column": "online", "name": "Сайт", "scale": "million"},
                            {"column": "offline", "name": "Остальные", "scale": "million"},
                            {"column": "online_share", "name": "Доля сайта", "number_format": "0%"},
                        ],
                    }
                ],
            },
            {
                "example": 258,
                "markers": {"Доля": {"metric": "top_share", "percent": True, "decimals": 0}},
                "blocks": [
                    {
                        "type": "chart_fill",
                        "shape": 4,
                        "dataset": "by_region",
                        "categories": "region",
                        "series": [{"column": "revenue", "name": "Выручка"}],
                    }
                ],
            },
            {
                "example": 261,
                "blocks": [
                    {
                        "type": "table_fill",
                        "shape": 3,
                        "dataset": "region_months",
                        "columns": [{"column": "region", "header": "Регион"}],
                        "other_columns": True,
                        "number": {"scale": "million", "decimals": 1},
                    }
                ],
            },
            {
                "layout": "title_only",
                "blocks": [
                    {"type": "text", "slot": "title", "text": "Лучшие менеджеры: {{ period.label }}"},
                    {
                        "type": "chart",
                        "slot": "body",
                        "chart": "bar",
                        "dataset": "top_managers",
                        "x": "manager",
                        "series": [{"column": "revenue", "name": "Выручка, млн"}],
                        "number_format": "#,##0.0,,",
                        "data_labels": True,
                    },
                ],
            },
            {
                "layout": "title_and_content",
                "blocks": [
                    {"type": "text", "slot": "title", "text": "Менеджеры: {{ period.label }}"},
                    {"type": "table", "slot": "body", "dataset": "top_managers"},
                ],
            },
            {"example": 263},
        ],
    }


def run_once(histories: dict[str, Any], work: Path, cache: Path | None, name: str) -> tuple[Any, float]:
    from autogenerator.contracts import RunRequest
    from autogenerator.worker import run

    req = RunRequest(
        scenario=scenario(),
        sources=[s for s in sources() if s.id == "sales"],
        histories={"sales": histories["sales"]},
        theme=str(TEMPLATE),
        output=str(work / f"{name}.pptx"),
        cache_dir=str(cache) if cache else None,
        temp_dir=str(work / "tmp"),
    )
    t0 = time.perf_counter()
    res = run(req)
    seconds = time.perf_counter() - t0
    if not res.output_path:
        raise SystemExit(f"Отчёт не собран: {'; '.join(str(e) for e in res.errors)}")
    return res, seconds


@contextlib.contextmanager
def profiled() -> Iterator[dict[str, float]]:
    """Время записи Parquet (Polars) и запросов DuckDB во время запуска: где тратится время
    узла входа. Только для замера: подменяет методы на время блока."""
    import polars as pl

    from autogenerator.engine.duck import Duck

    times: dict[str, float] = defaultdict(float)
    sink, query = pl.LazyFrame.sink_parquet, Duck.run

    def timed_sink(self: Any, path: Any, *a: Any, **k: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return sink(self, path, *a, **k)
        finally:
            name = Path(str(path)).name
            what = "кэш узла" if name.endswith(".tmp") else name.rsplit("-", 1)[0]
            times[f"Polars → Parquet: {what}"] += time.perf_counter() - t0

    def timed_query(self: Any, q: str, *a: Any, **k: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return query(self, q, *a, **k)
        finally:
            times[f"DuckDB: {' '.join(q.split())[:60]}"] += time.perf_counter() - t0

    setattr(pl.LazyFrame, "sink_parquet", timed_sink)  # noqa: B010 — подмена только на время замера
    setattr(Duck, "run", timed_query)  # noqa: B010
    try:
        yield times
    finally:
        setattr(pl.LazyFrame, "sink_parquet", sink)  # noqa: B010
        setattr(Duck, "run", query)  # noqa: B010


def run(root: Path, uploads: int, rows: int, profile: bool = False) -> list[dict[str, Any]]:
    work = Path(tempfile.mkdtemp(prefix="agen-bench-run-"))
    results: list[dict[str, Any]] = []
    try:
        histories = manifests(root, uploads, rows)
        print("Запуск…", flush=True)
        times: dict[str, float] = {}
        with profiled() if profile else contextlib.nullcontext(times) as times:
            res, s = run_once(histories, work, work / "cache", "cold")
        what = f"Отчёт без кэша ({res.slides} слайдов)"
        results.append({"what": what, "seconds": round(s, 2), "target": RUN_SECONDS})
        period = res.period.key if res.period else "?"
        print(f"  без кэша: {s:.1f} с, {res.slides} слайдов, период {period}", flush=True)
        for w in res.warnings:
            print(f"  ! {w}", flush=True)
        slow = sorted((n for n in res.nodes if n.seconds), key=lambda n: -(n.seconds or 0))[:8]
        for n in slow:
            print(f"    {n.seconds:6.1f} с  {n.id}", flush=True)
        for what, sec in sorted(times.items(), key=lambda x: -x[1]):
            print(f"    {sec:6.1f} с    {what}", flush=True)
        res, s = run_once(histories, work, work / "cache", "warm")
        results.append({"what": "Пересборка с кэшем узлов", "seconds": round(s, 2), "target": None})
        print(f"  с кэшем: {s:.1f} с", flush=True)
        results.append({"what": "Пиковая память процесса", "peak_bytes": peak_memory_bytes()})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results


def ok(r: dict[str, Any]) -> bool:
    target = r.get("target")
    return "seconds" not in r or target is None or float(r["seconds"]) <= float(target)


def report(results: list[dict[str, Any]], uploads: int, rows: int) -> str:
    lines = [
        f"Бенчмарк запуска — {platform.system()} {platform.release()}, {os.cpu_count()} ядер, "
        f"Python {platform.python_version()}; история {uploads} × {num(rows)} строк",
        "",
        "| Замер | Значение | Цель | Итог |",
        "|---|---|---|---|",
    ]
    for r in results:
        if "seconds" in r:
            target, verdict = ("—", "—") if r["target"] is None else (f"{r['target']:.0f} с", "да" if ok(r) else "НЕТ")
            lines.append(f"| {r['what']} | {r['seconds']:.1f} с | {target} | {verdict} |")
        else:
            lines.append(f"| {r['what']} | {r['peak_bytes'] / 2**30:.2f} ГБ | — | — |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench_run")
    ap.add_argument("--data", type=Path, default=Path(tempfile.gettempdir()) / "agen-bench-data")
    ap.add_argument("--uploads", type=int, default=5)
    ap.add_argument("--rows", type=int, default=10_000_000, help="строк в одной загрузке")
    ap.add_argument("--report", type=Path, help="записать отчёт в файл (Markdown)")
    ap.add_argument("--check", action="store_true", help="код выхода 1, если цель не достигнута")
    ap.add_argument("--profile", action="store_true", help="время записи Parquet и запросов DuckDB")
    a = ap.parse_args(argv)
    root = ensure_history(a.data, a.uploads, a.rows)
    results = run(root, a.uploads, a.rows, a.profile)
    text = report(results, a.uploads, a.rows)
    print(text)
    if a.report:
        a.report.write_text(text, encoding="utf-8")
    if a.check and not all(ok(r) for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
