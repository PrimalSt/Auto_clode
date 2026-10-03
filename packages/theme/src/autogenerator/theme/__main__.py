"""``python -m autogenerator.theme шаблон.pptx`` — что приложение видит в шаблоне.

Показывает размер слайда, макеты всех мастеров, какие макеты взяты под роли (титульный,
заголовок и блок, два блока, …), слайды-образцы с метками ``{{…}}``, графиками и таблицами,
шрифты и отчёт проверки шаблона. Шаблон не меняется: работа идёт с копией во временной папке.
С ``--out манифест.json`` копия сохраняется рядом (``манифест.template.pptx``): по манифесту
отчёт собирает ``python -m autogenerator.render``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from autogenerator.contracts import AgenError

from .importer import import_template
from .report import describe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autogenerator.theme")
    ap.add_argument("template")
    ap.add_argument("--out", help="сохранить манифест в JSON")
    ap.add_argument("--layouts", action="store_true", help="показать все макеты с плейсхолдерами")
    ap.add_argument("--verbose", action="store_true", help="все метки и все заметки проверки")
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else None
    copy = out.with_name(out.stem + ".template.pptx").resolve() if out else None
    if copy is not None and copy == Path(a.template).resolve():
        print(f"Ошибка: рабочая копия перезаписала бы сам шаблон {copy}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        try:
            m = import_template(a.template, tmp)
        except AgenError as e:
            print(f"Ошибка: {e}", file=sys.stderr)
            return 1
        if out is not None and copy is not None:
            # Манифест ссылается на рабочую копию: она переезжает из временной папки к нему.
            shutil.copyfile(m.pptx_path, copy)
            m.pptx_path = str(copy)
    print(describe(m, layouts=a.layouts, verbose=a.verbose, name=Path(a.template).name))
    if out is not None:
        out.write_text(m.model_dump_json(indent=2), encoding="utf-8")
        print(f"Манифест сохранён: {out} (рабочая копия шаблона: {copy})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
