"""``python -m autogenerator.ingest выгрузка.csv`` — структура файла без остального приложения.

Показывает, как файл прочитан (кодировка, разделитель, листы, строка заголовков), какие
типы выведены для столбцов и профиль столбцов по выборке.
"""

from __future__ import annotations

import argparse
import sys

from autogenerator.contracts import AgenError, ReadOptions
from autogenerator.plugin_host import PluginRegistry

from .reading import inspect_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.ingest")
    ap.add_argument("file")
    ap.add_argument("--format", help="csv, xlsx или xls; по умолчанию — по файлу")
    ap.add_argument("--encoding")
    ap.add_argument("--delimiter")
    ap.add_argument("--no-quote", action="store_true", help="в CSV нет кавычек")
    ap.add_argument("--header-row", type=int, help="строка заголовков; по умолчанию — найти")
    ap.add_argument("--sheet", action="append", help="лист Excel; можно несколько")
    ap.add_argument("--json", action="store_true", help="снимок структуры в JSON")
    a = ap.parse_args(argv)
    sheet: str | list[str | int] | None = None
    if a.sheet:
        sheet = a.sheet[0] if len(a.sheet) == 1 else list(a.sheet)
    opts = ReadOptions(
        encoding=a.encoding,
        delimiter=a.delimiter,
        quote=None if a.no_quote else '"',
        header_row=a.header_row,
        sheet=sheet,
    )
    try:
        snap = inspect_file(a.file, PluginRegistry.discover(), opts, fmt=a.format)
    except AgenError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(snap.model_dump_json(indent=2))
        return 0
    o = snap.options
    how = f"кодировка {o.encoding}, разделитель {o.delimiter!r}" if o.encoding else f"листы {', '.join(snap.sheets)}"
    print(f"{snap.path}: формат {snap.format}, {how}, заголовки в строке {o.header_row}")
    print(f"Выборка для типов: {snap.sample_rows} строк ({', '.join(snap.sample_parts)}); строк ≈ {snap.rows_estimate}")
    for c in snap.columns:
        fmt = f" ({c.format})" if c.format else ""
        p = c.profile
        prof = f"  пустых {p.nulls}, уникальных {p.unique}, {p.min} … {p.max}" if p else ""
        print(f"  {c.source_name:<30} {c.dtype.value:<9}{fmt:<12}{prof}")
    for n in snap.notes:
        print(f"  · {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
