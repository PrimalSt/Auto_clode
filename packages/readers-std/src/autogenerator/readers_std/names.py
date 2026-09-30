"""Общие мелочи читателей."""

from __future__ import annotations

from collections.abc import Sequence


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
