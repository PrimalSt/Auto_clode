"""Общее у читателей: названия столбцов и поиск строки заголовков."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

# Ячейка «похожа на число или дату», а не на название столбца.
_VALUE_LIKE = re.compile(r"^[\s\d.,:;/()+\-−%  ]*$")
HEADER_SCAN_ROWS = 30


def dedupe_names(names: Sequence[str | None]) -> list[str]:
    """Пустые названия — «Столбец N», повторы — с суффиксом « (2)», « (3)»."""
    out: list[str] = []
    used: set[str] = set()
    for i, raw in enumerate(names, start=1):
        base = (raw or "").strip() or f"Столбец {i}"
        name, n = base, 1
        while name in used:
            n += 1
            name = f"{base} ({n})"
        used.add(name)
        out.append(name)
    return out


def _is_text(cell: str | None) -> bool:
    return bool(cell and cell.strip()) and not _VALUE_LIKE.match(cell or "")


def data_width(rows: Sequence[Sequence[str | None]]) -> int:
    """Ширина таблицы: самое частое число заполненных до конца ячеек среди строк выборки."""
    widths = []
    for r in rows:
        w = len(r)
        while w and not (r[w - 1] or "").strip():
            w -= 1
        if w:
            widths.append(w)
    if not widths:
        return 0
    common = Counter(widths).most_common()
    best = max(common, key=lambda wc: (wc[1], wc[0]))
    return best[0]


def detect_header_row(rows: Sequence[Sequence[str | None]], width: int | None = None) -> int | None:
    """Номер строки заголовков (с единицы): первая строка, где большинство ячеек таблицы
    заполнено текстом (ARCHITECTURE.md, раздел 6.1, п. 3). Строки с заголовком отчёта над
    шапкой («Продажи за январь») и пустые строки пропускаются. ``None`` — не нашлась."""
    rows = list(rows)[:HEADER_SCAN_ROWS]
    width = width or data_width(rows)
    if width == 0:
        return None
    for i, r in enumerate(rows, start=1):
        cells = list(r)[:width]
        texts = sum(1 for c in cells if _is_text(c))
        if texts * 2 > width:
            return i
    return None
