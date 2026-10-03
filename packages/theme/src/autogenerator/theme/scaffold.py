"""Заготовка слайдов сценария по шаблону: все слайды-образцы с их метками, графиками и
таблицами, которые осталось привязать (``agen theme scaffold``).

Метки, которые встречаются на нескольких слайдах, выносятся в ``markers`` сценария (на всю
презентацию); месяц и год сразу привязаны к периоду. Остальные значения — пустые строки:
``agen validate`` покажет, что ещё не привязано.
"""

from __future__ import annotations

import json
from collections import Counter

from autogenerator.contracts import ChartInfo, TemplateSlideInfo, ThemeManifest

PERIOD_GUESS = {
    "месяц": "period.month",
    "month": "period.month",
    "год": "period.year",
    "year": "period.year",
    "год_прошлый": "period.prev_year",
    "прошлый_год": "period.prev_year",
    "квартал": "period.quarter_roman",
}


def _q(text: str) -> str:
    """Ключ или значение YAML в кавычках, если нужно: имена меток бывают вроде «lk+» и «+p»."""
    plain = text and text[0].isalpha() and all(ch.isalnum() or ch in "_ " for ch in text)
    return text if plain else json.dumps(text, ensure_ascii=False)


def _guess(name: str) -> str | None:
    return PERIOD_GUESS.get(name.strip().lower().replace(" ", "_"))


def _chart_note(c: ChartInfo) -> str:
    groups = " + ".join(f"{g.kind}{'/' + g.grouping if g.grouping else ''}×{len(g.series)}" for g in c.groups)
    extra = "; категории закреплены надписями" if c.labels_per_category else ""
    extra += f"; наложен на {', '.join(map(str, c.overlaid))}" if c.overlaid else ""
    return f"«{c.shape_name}»: {groups}, категорий {c.categories}{extra}"


def _slide(s: TemplateSlideInfo, common: set[str]) -> list[str]:
    out = [f"  - example: {s.slide_id}    # {s.number}. «{(s.title or '—')[:50]}»"]
    own = [n for n in s.marker_names if n not in common]
    if own:
        out.append("    markers:")
        for name in own:
            m = next(m for m in s.markers if m.name == name)
            guess = _guess(name)
            value = guess or '""'
            after = f" «{{{{{name}}}}}{m.text_after[:20]}»" if m.text_after else ""
            out.append(f"      {_q(name)}: {value}    #{after} в «{m.shape_name}»")
    if s.charts or s.tables:
        out.append("    blocks:")
    for c in s.charts:
        series = ", ".join('""' for _ in range(sum(len(g.series) for g in c.groups)))
        out += [
            "      - type: chart_fill",
            f"        shape: {c.shape_id}    # {_chart_note(c)}",
            '        dataset: ""',
            '        categories: ""',
            f"        series: [{series}]",
        ]
    for t in s.tables:
        out += [
            "      - type: table_fill",
            f"        shape: {t.shape_id}    # «{t.shape_name}»: {t.rows} × {t.cols}",
            '        dataset: ""',
        ]
    return out


def scaffold(m: ThemeManifest, name: str | None = None) -> str:
    counts = Counter(n for s in m.slides for n in s.marker_names)
    common = {n for n, k in counts.items() if k > 1}
    lines = [
        f"# Слайды шаблона {name or m.source_path}: оставьте нужные в slides сценария, впишите",
        "# показатели меток и наборы графиков и таблиц, затем проверьте: agen validate сценарий.yaml",
    ]
    if common:
        lines += ["", "markers:    # метки, которые есть на нескольких слайдах"]
        for n in sorted(common, key=lambda x: (_guess(x) is None, x)):
            where = ", ".join(str(s.number) for s in m.slides if n in s.marker_names)
            lines.append(f"  {_q(n)}: {_guess(n) or chr(34) * 2}    # слайды {where}")
    lines += ["", "slides:"]
    for s in m.slides:
        lines += _slide(s, common)
    return "\n".join(lines) + "\n"
