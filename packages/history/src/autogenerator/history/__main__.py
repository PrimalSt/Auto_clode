"""``python -m autogenerator.history манифест.json`` — история входа без остального приложения.

Манифест истории пишет ``agen run --workdir папка`` в ``папка/manifests/<вход>.json``.
Показывает загрузки, покрытие и отчётный период по умолчанию; с ``--out`` сохраняет
действующую историю в один Parquet.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl

from autogenerator.contracts import AgenError, HistoryManifest

from .view import coverage, default_report_period, history_view


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.history")
    ap.add_argument("manifest")
    ap.add_argument("--lower", type=date.fromisoformat, help="нижняя граница, ГГГГ-ММ-ДД")
    ap.add_argument("--upper", type=date.fromisoformat, help="верхняя граница (не включая)")
    ap.add_argument("--out", help="записать действующую историю в .parquet")
    a = ap.parse_args(argv)
    m = HistoryManifest.model_validate_json(Path(a.manifest).read_text(encoding="utf-8"))
    print(f"Источник {m.source_id}: тип периода {m.period_type}, правило {m.overlap_policy}")
    for u in m.uploads:
        print(f"  #{u.seq} {u.id}: {u.period.key} ({u.rows} строк, {u.status}) {u.original_name}")
    print("Покрытие: " + ", ".join(f"{s.start}..{s.end_exclusive}" for s in coverage(m)))
    try:
        print(f"Отчётный период по умолчанию: {default_report_period(m).key}")
        lf = history_view(m, lower=a.lower, upper_exclusive=a.upper)
        print(f"Строк в действующей истории: {lf.select(pl.len()).collect().item()}")
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            lf.sink_parquet(a.out)
            print(f"Записано: {a.out}")
    except AgenError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
