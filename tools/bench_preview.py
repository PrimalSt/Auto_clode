"""Бенчмарк превью (PRD, раздел 9; ARCHITECTURE.md, разделы 6.4, 7 и 13).

Цель для компьютера пользователя: превью шага не дольше 2 с (на выборке), точные числа —
в фоне. История — 5 загрузок по 10 млн строк (50 млн) месячных продаж; часть номеров заказов
повторяется между загрузками и внутри загрузки. Второй вход — возвраты (2% заказов),
с которым продажи объединяются по номеру заказа.

Файлы пишутся сразу в том виде, в каком их оставляет ``agen upload add`` (Parquet по месяцам
со служебными столбцами), и превью идёт через то же задание исполнителя, что и в приложении.
Замеры в одном процессе: исполнитель в приложении живёт между превью, а выборка и
результаты узлов лежат в кэше папки данных.

1. Первое превью шага: выборка строится по всей истории и кладётся в кэш (холодный старт).
2. Превью каждого шага на готовой выборке — цель 2 с.
3. Превью после правки шага (другой порог фильтра) — цель 2 с.
4. Точное превью без выборки — так считаются числа «в фоне»; цели нет, время для сравнения.

Проверяется и выборка: число строк после удаления дубликатов и число строк с парой при
объединении, пересчитанные с выборки, отличаются от точных не больше чем на 5%.

Примеры::

    uv run python tools/bench_preview.py                            # 5 × 10 млн строк
    uv run python tools/bench_preview.py --rows 2000000             # 5 × 2 млн
    uv run python tools/bench_preview.py --check --report bench.md  # код 1, если цель не достигнута
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from bench_ingest import peak_memory_bytes  # noqa: E402

STEP_SECONDS = 2.0
SAMPLE_TOLERANCE = 0.05
CHUNK_ROWS = 1_000_000
FIRST_MONTH = date(2026, 1, 1)

REGIONS = ["Север", "Юг", "Запад", "Восток", "Центр", "Урал", "Сибирь", "Дальний Восток"]
CHANNELS = ["Сайт", "Офис", "Партнёр", "Маркетплейс"]
STATUSES = ["Оплачен", "Отгружен", "Доставлен", "Отменён"]

SALES_COLUMNS = {
    "date": "date",
    "order_no": "string",
    "region": "string",
    "city": "string",
    "manager": "string",
    "client": "string",
    "channel": "string",
    "product": "string",
    "status": "string",
    "qty": "int",
    "price": "float",
    "amount": "float",
    "discount": "float",
    "cost": "float",
}
RETURNS_COLUMNS = {"date": "date", "order_no": "string", "return_amount": "float"}
NET = "amount - coalesce(return_amount, 0)"
STEPS = ("dedupe", "positive", "returns", "net", "returned")


def num(n: float) -> str:
    """Число с пробелами между разрядами: 10 000 000."""
    return f"{n:,.0f}".replace(",", " ")


def month_of(i: int) -> date:
    m = FIRST_MONTH.month - 1 + i
    return date(FIRST_MONTH.year + m // 12, m % 12 + 1, 1)


def _pick(values: list[str], idx: pl.Expr) -> pl.Expr:
    return idx.replace_strict(dict(enumerate(values)), return_dtype=pl.String)


def _order(n: pl.Expr) -> pl.Expr:
    return pl.concat_str([pl.lit("З-"), n.cast(pl.String).str.zfill(10)])


def sales_chunk(start: int, n: int, upload: int, rows: int) -> pl.DataFrame:
    """Строки ``start … start+n`` загрузки ``upload`` (с нуля): уже с типами, как после чтения."""
    i = pl.int_range(start, start + n, dtype=pl.UInt64, eager=True).alias("i").to_frame()
    idx = pl.col("i")
    seed = 7919 * (upload + 1)

    def rnd(k: int, salt: int) -> pl.Expr:
        return (idx.hash(seed + salt) % k).cast(pl.Int64)

    month = month_of(upload)
    days = (month_of(upload + 1) - month).days
    # 5% строк повторяют заказы прошлой загрузки, 1% — заказы этой же.
    order = (
        pl.when((rnd(100, 1) < 5) & (upload > 0))
        .then((upload - 1) * rows + rnd(rows, 2))
        .when(rnd(100, 3) < 1)
        .then(upload * rows + rnd(max(start + n, 1), 4))
        .otherwise(upload * rows + idx.cast(pl.Int64))
    )
    qty = rnd(20, 5) + 1
    price = (rnd(50_000, 6) + 100).cast(pl.Float64)
    amount = pl.when(rnd(100, 7) < 2).then(-qty * price).otherwise(qty * price)
    return i.select(
        (pl.lit(month) + pl.duration(days=rnd(days, 8))).alias("date"),
        _order(order).alias("order_no"),
        _pick(REGIONS, rnd(len(REGIONS), 9)).alias("region"),
        pl.concat_str([pl.lit("Город "), rnd(60, 10).cast(pl.String)]).alias("city"),
        pl.concat_str([pl.lit("Менеджер "), rnd(300, 11).cast(pl.String)]).alias("manager"),
        pl.concat_str([pl.lit("Клиент "), rnd(50_000, 12).cast(pl.String)]).alias("client"),
        _pick(CHANNELS, rnd(len(CHANNELS), 13)).alias("channel"),
        pl.concat_str([pl.lit("Товар "), rnd(5_000, 14).cast(pl.String)]).alias("product"),
        _pick(STATUSES, rnd(len(STATUSES), 15)).alias("status"),
        qty.alias("qty"),
        price.alias("price"),
        amount.alias("amount"),
        (rnd(4, 16) * 5).cast(pl.Float64).alias("discount"),
        (amount * (60 + rnd(25, 17)) / 100).alias("cost"),
        pl.lit(f"bench-sales-{upload + 1}").alias("_upload_id"),
        pl.lit(upload + 1, dtype=pl.Int32).alias("_upload_seq"),
        (idx.cast(pl.Int64) + 1).alias("_row"),
    )


def returns_frame(upload: int, rows: int) -> pl.DataFrame:
    """Возвраты месяца: каждый 50-й заказ загрузки продаж."""
    n = max(rows // 50, 1)
    i = pl.int_range(0, n, dtype=pl.UInt64, eager=True).alias("i").to_frame()
    idx = pl.col("i")
    month = month_of(upload)
    days = (month_of(upload + 1) - month).days
    return i.select(
        (pl.lit(month) + pl.duration(days=(idx.hash(31) % days).cast(pl.Int64))).alias("date"),
        _order(upload * rows + (idx.hash(37) % rows).cast(pl.Int64)).alias("order_no"),
        ((idx.hash(41) % 10_000).cast(pl.Float64) + 1).alias("return_amount"),
        pl.lit(f"bench-returns-{upload + 1}").alias("_upload_id"),
        pl.lit(upload + 1, dtype=pl.Int32).alias("_upload_seq"),
        (idx.cast(pl.Int64) + 1).alias("_row"),
    )


def ensure_history(folder: Path, uploads: int, rows: int) -> Path:
    """История двух входов в папке ``folder``; уже готовая не пересоздаётся."""
    root = folder / f"history-{uploads}x{rows}"
    done = root / "done"
    if done.exists():
        return root
    shutil.rmtree(root, ignore_errors=True)
    t0 = time.perf_counter()
    print(f"Генерация истории: {uploads} загрузок по {num(rows)} строк…", flush=True)
    for u in range(uploads):
        month = f"month={month_of(u):%Y-%m}"
        out = root / "sales" / f"u{u + 1}" / month
        out.mkdir(parents=True)
        for k, start in enumerate(range(0, rows, CHUNK_ROWS)):
            sales_chunk(start, min(CHUNK_ROWS, rows - start), u, rows).write_parquet(out / f"part-{k}.parquet")
        out = root / "returns" / f"u{u + 1}" / month
        out.mkdir(parents=True)
        returns_frame(u, rows).write_parquet(out / "part-0.parquet")
    size = sum(f.stat().st_size for f in root.rglob("*.parquet"))
    print(f"  готово за {time.perf_counter() - t0:.0f} с, {num(size / 2**20)} МБ", flush=True)
    done.write_text("ok", encoding="utf-8")
    return root


def sources() -> list[Any]:
    from autogenerator.contracts import SourceSpec

    def spec(sid: str, columns: dict[str, str], keys: list[str]) -> SourceSpec:
        return SourceSpec.model_validate(
            {
                "id": sid,
                "name": sid,
                "format": "csv",
                "period_column": "date",
                "period_type": "month",
                "overlap_policy": "replace_period",
                "keys": keys,
                "columns": [{"id": c, "name": c, "dtype": t} for c, t in columns.items()],
            }
        )

    return [spec("sales", SALES_COLUMNS, ["order_no"]), spec("returns", RETURNS_COLUMNS, [])]


def manifests(root: Path, uploads: int, rows: int) -> dict[str, Any]:
    from autogenerator.contracts import HistoryManifest, Period, UploadRef

    out = {}
    for s in sources():
        refs = [
            UploadRef(
                id=f"bench-{s.id}-{u + 1}",
                seq=u + 1,
                uri=str(root / s.id / f"u{u + 1}"),
                period=Period.parse(f"{month_of(u):%Y-%m}"),
                rows=rows if s.id == "sales" else max(rows // 50, 1),
                original_name=f"{s.id}_{month_of(u):%Y-%m}.csv",
            )
            for u in range(uploads)
        ]
        out[s.id] = HistoryManifest.for_source(s, refs)
    return out


def scenario(threshold: float) -> Any:
    from autogenerator.contracts import ScenarioSpec

    return ScenarioSpec.model_validate(
        {
            "name": "Бенчмарк превью",
            "inputs": [
                {
                    "id": "sales",
                    "source": "sales",
                    "main": True,
                    "pipeline": [
                        {"id": "dedupe", "type": "dedupe", "by": ["order_no"], "keep": "last"},
                        {"id": "positive", "type": "filter", "where": f"amount > {threshold}"},
                        {"id": "returns", "type": "join", "with": "returns", "on": ["order_no"]},
                        {"id": "net", "type": "formula", "column": "net", "expr": NET},
                        # Строки с парой во входе возвратов: так проверяется, что выборка их сохраняет.
                        {"id": "returned", "type": "filter", "where": "return_amount IS NOT NULL"},
                    ],
                },
                {"id": "returns", "source": "returns"},
            ],
            "metrics": [{"id": "revenue", "input": "sales", "fn": "sum", "column": "net"}],
        }
    )


class Bench:
    def __init__(self, root: Path, work: Path, uploads: int, rows: int):
        self.histories = manifests(root, uploads, rows)
        self.cache = work / "cache"
        self.temp = work / "tmp"

    def preview(self, target: str, threshold: float = 0, sample: int | None = None) -> tuple[Any, float]:
        from autogenerator.contracts import PreviewRequest
        from autogenerator.worker import preview

        req = PreviewRequest(
            scenario=scenario(threshold),
            sources=sources(),
            histories=self.histories,
            target=target,
            rows=20,
            sample=sample,
            cache_dir=str(self.cache),
            temp_dir=str(self.temp),
        )
        t0 = time.perf_counter()
        res = preview(req)
        seconds = time.perf_counter() - t0
        if res.errors:
            raise SystemExit(f"Превью {target} с ошибкой: {'; '.join(str(e) for e in res.errors)}")
        return res, seconds


def rows_after(res: Any, step: str) -> int:
    return next(int(s.rows_after) for s in res.steps if s.id == step)


def run(root: Path, uploads: int, rows: int) -> list[dict[str, Any]]:
    work = Path(tempfile.mkdtemp(prefix="agen-bench-preview-"))
    results: list[dict[str, Any]] = []
    try:
        b = Bench(root, work, uploads, rows)

        def note(what: str, res: Any, seconds: float, target: float | None) -> None:
            sample = f"≈1/{res.sample.k}" if res.sample else "без выборки"
            r = {"what": what, "seconds": round(seconds, 2), "target": target, "sample": sample}
            results.append(r)
            print(f"  {what}: {seconds:.2f} с ({sample})", flush=True)

        print("Превью…", flush=True)
        cold, s = b.preview("input:sales/step:dedupe")
        note("Первое превью: выборка по всей истории", cold, s, None)
        if cold.sample is None:
            raise SystemExit("Превью на большой истории построено без выборки")
        for step in STEPS:
            sampled, s = b.preview(f"input:sales/step:{step}")
            note(f"Превью шага «{step}»", sampled, s, STEP_SECONDS)
        res, s = b.preview("input:sales/step:positive", threshold=100)
        note("Превью после правки фильтра", res, s, STEP_SECONDS)

        exact, s = b.preview(f"input:sales/step:{STEPS[-1]}", sample=1)
        note("Точное превью входа (числа «в фоне»)", exact, s, None)
        for label, a, e in (
            ("строк после удаления дубликатов", rows_after(sampled, "dedupe"), rows_after(exact, "dedupe")),
            ("строк с парой при объединении", rows_after(sampled, "returned"), rows_after(exact, "returned")),
        ):
            err = abs(a - e) / e if e else 0.0
            ok = err <= SAMPLE_TOLERANCE
            results.append({"what": f"Выборка: {label}", "estimate": a, "exact": e, "error": err, "ok": ok})
            print(f"  выборка, {label}: ≈{num(a)} при точном {num(e)} ({err:.1%})", flush=True)
        results.append({"what": "Пиковая память процесса", "peak_bytes": peak_memory_bytes()})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results


def ok(r: dict[str, Any]) -> bool:
    if "ok" in r:
        return bool(r["ok"])
    target = r.get("target")
    return target is None or float(r["seconds"]) <= float(target)


def yes(flag: bool) -> str:
    return "да" if flag else "НЕТ"


def report(results: list[dict[str, Any]], uploads: int, rows: int) -> str:
    lines = [
        f"Бенчмарк превью — {platform.system()} {platform.release()}, {os.cpu_count()} ядер, "
        f"Python {platform.python_version()}; история {uploads} × {num(rows)} строк",
        "",
        "| Замер | Время | Цель | Итог |",
        "|---|---|---|---|",
    ]
    for r in results:
        if "seconds" in r:
            target, verdict = ("—", "—") if r["target"] is None else (f"{r['target']:.0f} с", yes(ok(r)))
            lines.append(f"| {r['what']} ({r['sample']}) | {r['seconds']:.2f} с | {target} | {verdict} |")
        elif "exact" in r:
            lines.append(
                f"| {r['what']}: ≈{num(r['estimate'])} при точном {num(r['exact'])} | {r['error']:.1%} | "
                f"до {SAMPLE_TOLERANCE:.0%} | {yes(ok(r))} |"
            )
        else:
            lines.append(f"| {r['what']} | {r['peak_bytes'] / 2**30:.2f} ГБ | — | — |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench_preview")
    ap.add_argument("--data", type=Path, default=Path(tempfile.gettempdir()) / "agen-bench-data")
    ap.add_argument("--uploads", type=int, default=5)
    ap.add_argument("--rows", type=int, default=10_000_000, help="строк в одной загрузке")
    ap.add_argument("--report", type=Path, help="записать отчёт в файл (Markdown)")
    ap.add_argument("--check", action="store_true", help="код выхода 1, если цель не достигнута")
    a = ap.parse_args(argv)
    root = ensure_history(a.data, a.uploads, a.rows)
    results = run(root, a.uploads, a.rows)
    text = report(results, a.uploads, a.rows)
    print(text)
    if a.report:
        a.report.write_text(text, encoding="utf-8")
    if a.check and not all(ok(r) for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
