"""Проверка шаблона при загрузке (F-410, ARCHITECTURE.md, раздел 6.5).

Отчёт показывает места, которые нужно поправить в самом шаблоне до привязки: метки, которые
нельзя заменить, одинаковые метки на разных слайдах и на одном слайде, вписанные вручную
годы и номера слайдов, повторяющиеся имена фигур, фигуры за краем слайда, макеты без
геометрии и шрифты, которых нет на компьютере. Шаблон поправляется в PowerPoint и
загружается повторно.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from lxml import etree

from autogenerator.contracts import FontInfo, IssueLevel, LayoutInfo, LintIssue, TemplateSlideInfo
from autogenerator.contracts.ooxml import LOOSE_RE, A, P, iter_shapes, paragraph_segments
from autogenerator.contracts.theme import EMU_PER_INCH

from .layouts import has_geometry

# Допуск для «за краем слайда»: без него предупреждение получит и логотип на обложке.
EDGE_TOLERANCE = EMU_PER_INCH // 100
YEAR_RE = re.compile(r"(?<![\d.,])(19[5-9]\d|20\d\d)(?![\d.,]\d)")
SLIDE_REF_RE = re.compile(r"\bслайд(?:е|а|у|ах|ы|ов)?\s*(?:№\s*)?\d+|\bslide\s*\d+", re.IGNORECASE)
SHOW = 6
REL_CHART = "/chart"
REL_DIAGRAM = "/diagramData"


def _issue(code: str, message: str, slide: TemplateSlideInfo | None = None, **kw: Any) -> LintIssue:
    return LintIssue(
        code=code,
        message=message,
        slide=slide.number if slide else None,
        slide_id=slide.slide_id if slide else None,
        **kw,
    )


def _few(items: list[str], show: int = SHOW) -> str:
    if len(items) <= show:
        return ", ".join(items)
    return ", ".join(items[:show]) + f" и ещё {len(items) - show}"


def _times(n: int) -> str:
    return f"{n} раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else f"{n} раз"


def _quote(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return f"«{text if len(text) <= limit else text[: limit - 1] + '…'}»"


def _paragraphs(slide: Any) -> list[tuple[int, str]]:
    """Текст абзацев всех фигур слайда (с ячейками таблиц): (id фигуры, текст)."""
    out = []
    for sh in iter_shapes(slide._element.find(f"{P}cSld/{P}spTree")):
        for p in sh.el.iter(f"{A}p"):
            text, _ = paragraph_segments(p)
            if text.strip():
                out.append((sh.id, text))
    return out


def _part_text(blob: bytes) -> list[str]:
    root = etree.fromstring(blob)
    return ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in root.iter(f"{A}p")]


def lint_markers(slides: list[TemplateSlideInfo]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    where: dict[str, list[int]] = defaultdict(list)
    for s in slides:
        for m in s.markers:
            if not m.replaceable:
                issues.append(
                    _issue(
                        "marker_unreplaceable",
                        f"метку {{{{{m.name}}}}} в фигуре «{m.shape_name}» нельзя заменить: {m.reason}",
                        s,
                        level=IssueLevel.ERROR,
                        shape_id=m.shape_id,
                    )
                )
        counts = Counter(m.name for m in s.markers)
        for name in counts:
            where[name].append(s.number)
        for name, n in counts.items():
            if n > 1:
                shapes = list(dict.fromkeys(m.shape_name for m in s.markers if m.name == name))
                issues.append(
                    _issue(
                        "duplicate_marker",
                        f"метка {{{{{name}}}}} встречается {_times(n)} ({_few(shapes)}): если значения разные, "
                        "привяжите вхождения по отдельности",
                        s,
                        level=IssueLevel.INFO,
                    )
                )
    for name, numbers in where.items():
        if len(numbers) > 1:
            issues.append(
                _issue(
                    "same_marker",
                    f"метка {{{{{name}}}}} есть на слайдах {_few([str(n) for n in numbers], 10)}: если на слайдах "
                    "разные значения, привяжите её на уровне слайда",
                    level=IssueLevel.INFO,
                )
            )
    return issues


def lint_objects(prs: Any, slides: list[TemplateSlideInfo]) -> list[LintIssue]:
    """Метки в графиках, SmartArt и заметках: их приложение не заменяет."""
    issues: list[LintIssue] = []
    for s, slide in zip(slides, prs.slides, strict=True):
        found: list[tuple[str, str]] = []
        for rel in slide.part.rels.values():
            if rel.is_external:
                continue
            if rel.reltype.endswith(REL_CHART):
                found += [("графике", t) for t in _part_text(rel.target_part.blob)]
            elif rel.reltype.endswith(REL_DIAGRAM):
                found += [("SmartArt", t) for t in _part_text(rel.target_part.blob)]
        if slide.has_notes_slide:
            found += [("заметках", p.text) for p in slide.notes_slide.notes_text_frame.paragraphs]
        for kind, text in found:
            for m in LOOSE_RE.finditer(text):
                issues.append(
                    _issue(
                        "marker_in_object",
                        f"метка {{{{{m.group(1).strip()}}}}} в {kind} не заменяется: перенесите её в надпись "
                        "или подпись данных",
                        s,
                        level=IssueLevel.ERROR,
                    )
                )
    return issues


def lint_text(prs: Any, slides: list[TemplateSlideInfo]) -> list[LintIssue]:
    """Вписанные вручную годы и ссылки на номера слайдов."""
    issues: list[LintIssue] = []
    for s, slide in zip(slides, prs.slides, strict=True):
        for shape_id, text in _paragraphs(slide):
            plain = LOOSE_RE.sub(" ", text)
            for y in dict.fromkeys(YEAR_RE.findall(plain)):
                issues.append(
                    _issue(
                        "hardcoded_year",
                        f"год {y} вписан вручную: {_quote(text)}; в следующем году он устареет — замените "
                        "его меткой, например {{Год}} или {{Год_прошлый}}",
                        s,
                        shape_id=shape_id,
                    )
                )
            for ref in dict.fromkeys(m.group(0) for m in SLIDE_REF_RE.finditer(plain)):
                issues.append(
                    _issue(
                        "slide_reference",
                        f"ссылка на номер слайда «{ref}»: {_quote(text)}; номера меняются, когда слайды "
                        "переставляют или удаляют",
                        s,
                        shape_id=shape_id,
                    )
                )
    return issues


def lint_shapes(prs: Any, slides: list[TemplateSlideInfo], width: int, height: int) -> list[LintIssue]:
    """Повторяющиеся имена фигур и фигуры за краем слайда."""
    issues: list[LintIssue] = []
    tol = EDGE_TOLERANCE
    for s, slide in zip(slides, prs.slides, strict=True):
        shapes = list(iter_shapes(slide._element.find(f"{P}cSld/{P}spTree")))
        names = Counter(sh.name for sh in shapes if sh.name)
        dups = [f"«{n}» × {c}" for n, c in names.items() if c > 1]
        if dups:
            issues.append(
                _issue(
                    "duplicate_shape_name",
                    f"повторяются имена фигур: {_few(dups)}; приложение различает фигуры по id, а в окне "
                    "привязки их проще найти по разным именам",
                    s,
                    level=IssueLevel.INFO,
                )
            )
        out = []
        for sh in shapes:
            g = sh.geometry
            if sh.in_group or g is None:
                continue
            if g.x < -tol or g.y < -tol or g.right > width + tol or g.bottom > height + tol:
                out.append(f"«{sh.name}»")
        if out:
            issues.append(
                _issue(
                    "off_slide",
                    f"за краем слайда: {_few(out)}; при показе эта часть не видна",
                    s,
                    level=IssueLevel.INFO,
                )
            )
    return issues


def lint_layouts(layouts: list[LayoutInfo]) -> list[LintIssue]:
    bad = [i for i in layouts if not has_geometry(i)]
    if not bad:
        return []
    used = [f"«{i.name}»" for i in bad if i.slides]
    names = [f"«{i.name}»" for i in bad]
    tail = f"; слайды-образцы на них ({_few(used)}) работают как обычно" if used else ""
    return [
        _issue(
            "layout_no_geometry",
            f"у плейсхолдеров {len(bad)} макетов нет размеров ни в макете, ни в мастере ({_few(names)}): для новых "
            f"слайдов они не предлагаются{tail}",
            level=IssueLevel.INFO,
        )
    ]


def lint_fonts(fonts: list[FontInfo]) -> list[LintIssue]:
    issues = []
    for f in fonts:
        if f.installed is False:
            extra = " (встроен в шаблон: PowerPoint его покажет)" if f.embedded else ""
            issues.append(
                _issue(
                    "font_missing",
                    f"шрифт «{f.name}» не установлен{extra}; без него проверки длины текста приблизительные",
                )
            )
    return issues


def lint(
    prs: Any,
    slides: list[TemplateSlideInfo],
    layouts: list[LayoutInfo],
    fonts: list[FontInfo],
    width: int,
    height: int,
) -> list[LintIssue]:
    return [
        *lint_markers(slides),
        *lint_objects(prs, slides),
        *lint_text(prs, slides),
        *lint_shapes(prs, slides, width, height),
        *lint_layouts(layouts),
        *lint_fonts(fonts),
    ]
