"""``python -m autogenerator.theme шаблон.pptx`` — что приложение видит в шаблоне.

Показывает размер слайда, макеты всех мастеров, какие макеты взяты под роли (титульный,
заголовок и блок, два блока, …) и слайды шаблона с метками ``{{…}}``, графиками и таблицами.
Шаблон не меняется: работа идёт с копией во временной папке.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from autogenerator.contracts import AgenError
from autogenerator.contracts.theme import EMU_PER_INCH

from .importer import import_template


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.theme")
    ap.add_argument("template")
    ap.add_argument("--out", help="сохранить манифест в JSON")
    ap.add_argument("--layouts", action="store_true", help="показать все макеты с плейсхолдерами")
    a = ap.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            m = import_template(a.template, tmp)
        except AgenError as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 1
    w, h = m.slide_width / EMU_PER_INCH, m.slide_height / EMU_PER_INCH
    masters = len({lay.master for lay in m.layouts})
    print(f"{Path(a.template).name}: слайд {w:.3f} × {h:.3f} дюйма, мастеров {masters}, макетов {len(m.layouts)}")
    print("Роли макетов (предложены приложением):")
    for r in m.roles:
        slots = ", ".join(s.name for s in r.slots)
        print(f"  {r.role:<22} → «{r.layout_name}» (id {r.layout_key}); области: {slots}")
    if a.layouts:
        print("Макеты:")
        for lay in m.layouts:
            phs = ", ".join(f"{p.type}#{p.idx}{'' if p.geometry else ' без геометрии'}" for p in lay.placeholders)
            print(f"  [{lay.master}] {lay.name} (id {lay.key}{', preserve' if lay.preserve else ''}): {phs}")
    if m.slides:
        print(f"Слайды шаблона ({len(m.slides)}):")
        for s in m.slides:
            extra = []
            if s.markers:
                extra.append(f"метки: {', '.join(s.markers)}")
            if s.charts:
                extra.append(f"графиков {s.charts}")
            if s.tables:
                extra.append(f"таблиц {s.tables}")
            print(f"  {s.number:>2}. id {s.slide_id} «{s.title or '—'}» {'; '.join(extra)}")
    for n in m.notes:
        print(f"Замечание: {n}")
    if a.out:
        Path(a.out).write_text(m.model_dump_json(indent=2), encoding="utf-8")
        print(f"Манифест сохранён: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
