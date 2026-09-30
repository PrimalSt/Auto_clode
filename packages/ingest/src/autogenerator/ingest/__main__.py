"""``python -m autogenerator.ingest выгрузка.csv`` — структура файла без остального приложения.

Показывает, как файл прочитан (кодировка, разделитель, строка заголовков), и какие типы
выведены для столбцов.
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
    ap.add_argument("--format", help="csv или xlsx; по умолчанию — по файлу")
    ap.add_argument("--encoding")
    ap.add_argument("--delimiter")
    ap.add_argument("--header-row", type=int, default=1)
    ap.add_argument("--sheet")
    ap.add_argument("--json", action="store_true", help="снимок структуры в JSON")
    a = ap.parse_args(argv)
    opts = ReadOptions(encoding=a.encoding, delimiter=a.delimiter, header_row=a.header_row, sheet=a.sheet)
    try:
        snap = inspect_file(a.file, PluginRegistry.discover(), opts, fmt=a.format)
    except AgenError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(snap.model_dump_json(indent=2))
        return 0
    o = snap.options
    print(
        f"{snap.path}: формат {snap.format}, кодировка {o.encoding or '—'}, "
        f"разделитель {o.delimiter!r}, заголовки в строке {o.header_row}"
    )
    print(f"Выборка для типов: {snap.sample_rows} строк")
    for c in snap.columns:
        fmt = f" ({c.format})" if c.format else ""
        print(f"  {c.source_name:<30} {c.dtype.value:<9}{fmt}  например: {', '.join(c.sample[:3])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
