"""Текстовый отчёт о шаблоне: роли макетов, слайды-образцы, шрифты и проверка (F-410).
Им пользуются ``python -m autogenerator.theme`` и ``agen theme show``."""

from __future__ import annotations

from collections import Counter

from autogenerator.contracts import IssueLevel, TemplateSlideInfo, ThemeManifest
from autogenerator.contracts.theme import EMU_PER_INCH

LEVEL = {IssueLevel.ERROR: "ошибка", IssueLevel.WARNING: "внимание", IssueLevel.INFO: "заметка"}


def _slide_line(s: TemplateSlideInfo, verbose: bool) -> list[str]:
    head = f"  {s.number:>2}. id {s.slide_id} «{(s.title or '—')[:60]}» (макет «{s.layout_name}»)"
    lines = [head]
    if s.markers:
        names = s.marker_names
        shown = ", ".join(f"{{{{{n}}}}}" for n in (names if verbose else names[:12]))
        more = "" if verbose or len(names) <= 12 else f" и ещё {len(names) - 12}"
        lines.append(f"      метки ({len(s.markers)}): {shown}{more}")
    for c in s.charts:
        groups = " + ".join(
            f"{g.kind}{'/' + g.grouping if g.grouping else ''}×{len(g.series)}{' (2-я ось)' if g.secondary else ''}"
            for g in c.groups
        )
        extra = []
        if c.labels_per_category:
            extra.append("категории закреплены надписями")
        if c.overlaid:
            extra.append(f"наложен на {', '.join(str(i) for i in c.overlaid)}")
        tail = f"; {', '.join(extra)}" if extra else ""
        lines.append(f"      график id {c.shape_id} «{c.shape_name}»: {groups}, категорий {c.categories}{tail}")
    for t in s.tables:
        lines.append(f"      таблица id {t.shape_id} «{t.shape_name}»: {t.rows} × {t.cols}")
    return lines


def describe(m: ThemeManifest, *, layouts: bool = False, verbose: bool = False, name: str | None = None) -> str:
    w, h = m.slide_width / EMU_PER_INCH, m.slide_height / EMU_PER_INCH
    masters = len({lay.master for lay in m.layouts})
    out = [
        f"{name or m.source_path}: слайд {w:.3f} × {h:.3f} дюйма, мастеров {masters}, макетов {len(m.layouts)}, "
        f"слайдов {len(m.slides)}",
        "Роли макетов:",
    ]
    for r in m.roles:
        slots = ", ".join(s.name for s in r.slots)
        how = f"; {r.derived}" if r.derived else ""
        mark = "" if not r.guessed else " (предложено)"
        out.append(f"  {r.role.value:<22} → «{r.layout_name}» (id {r.layout_key}){mark}; области: {slots}{how}")
    if layouts:
        out.append("Макеты:")
        for lay in m.layouts:
            phs = ", ".join(f"{p.type}#{p.idx}{'' if p.geometry else ' без геометрии'}" for p in lay.placeholders)
            flags = ", preserve" if lay.preserve else ""
            used = f", слайдов {lay.slides}" if lay.slides else ""
            out.append(f"  [{lay.master}] {lay.name} (id {lay.key}{flags}{used}): {phs or '—'}")
    if m.slides:
        total = sum(len(s.markers) for s in m.slides)
        names = len({mk.name for s in m.slides for mk in s.markers})
        charts = sum(len(s.charts) for s in m.slides)
        tables = sum(len(s.tables) for s in m.slides)
        out.append(f"Слайды-образцы: меток {total} ({names} разных имён), графиков {charts}, таблиц {tables}")
        for s in m.slides:
            out += _slide_line(s, verbose)
    if m.fonts:
        fonts = []
        for f in m.fonts:
            state = {True: "", False: ", не установлен", None: ""}[f.installed]
            fonts.append(f"{f.name}{' (встроен)' if f.embedded else ''}{state}")
        out.append(f"Шрифты: {'; '.join(fonts)}")
    if m.lint:
        counts = Counter(i.level for i in m.lint)
        summary = ", ".join(f"{LEVEL[lv]} — {counts[lv]}" for lv in IssueLevel if counts[lv])
        out.append(f"Проверка шаблона ({summary}):")
        order = {IssueLevel.ERROR: 0, IssueLevel.WARNING: 1, IssueLevel.INFO: 2}
        for i in sorted(m.lint, key=lambda i: (order[i.level], i.slide or 0)):
            if i.level == IssueLevel.INFO and not verbose and i.code == "same_marker":
                continue
            out.append(f"  [{LEVEL[i.level]}] {i}")
        hidden = sum(1 for i in m.lint if i.code == "same_marker")
        if hidden and not verbose:
            out.append(f"  …и {hidden} заметок об одинаковых метках на разных слайдах (--verbose покажет)")
    elif m.slides:
        out.append("Проверка шаблона: замечаний нет")
    out += [f"Замечание импорта: {n}" for n in m.notes]
    return "\n".join(out)
