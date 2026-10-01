"""Бенчмарк загрузки (PRD, раздел 9; ARCHITECTURE.md, разделы 7 и 13).

Проверяет целевые значения для компьютера пользователя:

- CSV 10 млн строк × 30 столбцов — не дольше 2 мин и не больше 2 ГБ памяти;
- лист Excel 1 млн строк × 30 столбцов — не дольше 3 мин и не больше 4 ГБ.

Каждый замер — отдельный процесс, который делает то же, что ``agen upload add``: проверка
файла (SHA-256), сверка шапки, чтение, приведение типов, запись Parquet по месяцам и точный
профиль. Пиковую память процесс сообщает сам (Windows — PeakWorkingSetSize, Linux и
macOS — ru_maxrss). Файлы генерируются ``make_big_data.py``, если их ещё нет.

Примеры::

    uv run python tools/bench_ingest.py                          # полные размеры
    uv run python tools/bench_ingest.py --csv-rows 1000000 --xlsx-rows 200000
    uv run python tools/bench_ingest.py --check --report bench.md  # код 1, если цель не достигнута

При меньшем числе строк цель по времени уменьшается пропорционально, по памяти — та же.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
TARGETS = {
    # формат: (строк в цели, секунд, ГБ памяти)
    "csv": (10_000_000, 120.0, 2.0),
    "xlsx": (1_000_000, 180.0, 4.0),
}


def peak_memory_bytes() -> int:
    """Пиковая память своего процесса."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # Типы аргументов обязательны: без них ctypes передаёт псевдодескриптор процесса
        # как 32-битное int, вызов в 64-битном Windows не срабатывает и память выходит 0.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(c.PeakWorkingSetSize)
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


def child(fmt: str, path: Path, home: Path) -> None:
    """Один замер: создать источник по файлу и загрузить его, как ``agen upload add``."""
    from autogenerator.api import Home

    t0 = time.perf_counter()
    with Home.open(home, write=True) as h:
        spec, _ = h.draft_source(path, "bench")
        h.create_source(spec)
        t1 = time.perf_counter()
        out = h.upload("bench", path)
    t2 = time.perf_counter()
    res = out.result.upload
    peak = peak_memory_bytes()
    if peak <= 0:
        raise SystemExit("Не удалось измерить пиковую память процесса")
    print(
        json.dumps(
            {
                "format": fmt,
                "rows": out.record.rows,
                "columns": len(spec.columns),
                "file_bytes": path.stat().st_size,
                "draft_seconds": round(t1 - t0, 1),
                "upload_seconds": round(t2 - t1, 1),
                "write_seconds": res.seconds,
                "parquet_bytes": out.record.data_bytes,
                "cast_errors": sum(c.errors for c in res.cast_issues),
                "peak_bytes": peak,
            }
        )
    )


def ensure_file(fmt: str, rows: int, folder: Path) -> Path:
    sys.path.insert(0, str(TOOLS))
    import make_big_data as gen

    month = date(2026, 1, 1)
    suffix = "csv" if fmt == "csv" else "xlsx"
    path = folder / f"Продажи_2026-01_{rows}.{suffix}"
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    print(f"Генерация {path.name}…", flush=True)
    if fmt == "csv":
        gen.write_csv(path, rows, 1, month, "cp1251", ";")
    else:
        gen.write_xlsx(path, rows, 1, month)
    print(f"  готово за {time.perf_counter() - t0:.0f} с, {path.stat().st_size / 2**20:.0f} МБ", flush=True)
    return path


def measure(fmt: str, path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="agen-bench-") as home:
        env = {**os.environ, "PYTHONUTF8": "1"}
        cmd = [sys.executable, __file__, "--child", fmt, str(path), home]
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, check=False)
        wall = time.perf_counter() - t0
    if proc.returncode != 0:
        raise SystemExit(f"Замер {fmt} упал:\n{proc.stdout}\n{proc.stderr}")
    data: dict[str, object] = json.loads(proc.stdout.strip().splitlines()[-1])
    data["wall_seconds"] = round(wall, 1)
    return data


def verdict(r: dict[str, object]) -> tuple[float, float, bool]:
    rows_target, seconds, gb = TARGETS[str(r["format"])]
    rows = int(str(r["rows"]))
    time_target = seconds * rows / rows_target
    ok = float(str(r["upload_seconds"])) <= time_target and int(str(r["peak_bytes"])) <= gb * 2**30
    return time_target, gb, ok


def report(results: list[dict[str, object]]) -> str:
    lines = [
        f"Бенчмарк загрузки — {platform.system()} {platform.release()}, {os.cpu_count()} ядер, "
        f"Python {platform.python_version()}",
        "",
        "| Формат | Строк × столбцов | Файл | Загрузка | Цель | Память | Цель | Итог |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        time_target, gb, ok = verdict(r)
        lines.append(
            f"| {r['format']} | {int(str(r['rows'])):,} × {r['columns']} | "
            f"{int(str(r['file_bytes'])) / 2**20:,.0f} МБ | {r['upload_seconds']} с "
            f"(запись {r['write_seconds']} с) | {time_target:.0f} с | "
            f"{int(str(r['peak_bytes'])) / 2**30:.2f} ГБ | {gb:.0f} ГБ | {'да' if ok else 'НЕТ'} |".replace(",", " ")
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench_ingest")
    ap.add_argument("--child", nargs=3, metavar=("FORMAT", "FILE", "HOME"), help=argparse.SUPPRESS)
    ap.add_argument("--data", type=Path, default=Path(tempfile.gettempdir()) / "agen-bench-data")
    ap.add_argument("--csv-rows", type=int, default=10_000_000)
    ap.add_argument("--xlsx-rows", type=int, default=1_000_000)
    ap.add_argument("--only", choices=["csv", "xlsx"])
    ap.add_argument("--report", type=Path, help="записать отчёт в файл (Markdown)")
    ap.add_argument("--check", action="store_true", help="код выхода 1, если цель не достигнута")
    a = ap.parse_args(argv)
    if a.child:
        fmt, file, home = a.child
        child(fmt, Path(file), Path(home))
        return 0
    results = []
    for fmt, rows in (("csv", a.csv_rows), ("xlsx", a.xlsx_rows)):
        if a.only and fmt != a.only:
            continue
        path = ensure_file(fmt, rows, a.data)
        print(f"Замер {fmt}: {path.name}…", flush=True)
        results.append(measure(fmt, path))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    text = report(results)
    print(text)
    if a.report:
        a.report.write_text(text, encoding="utf-8")
    if a.check and not all(verdict(r)[2] for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
