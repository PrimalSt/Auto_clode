"""Общее для разбора .pptx: пространства имён, абсолютная геометрия фигур внутри групп,
текст абзаца по прогонам и поиск меток ``{{…}}``.

Это часть контракта, а не служебный код: ``theme`` нумерует вхождения меток в манифесте
(``имя@фигура#номер``), а блок ``markers`` и ``render`` находят их по тем же номерам. Порядок
обхода фигур, абзацев и меток должен совпадать во всех модулях, поэтому он задан здесь.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from lxml import etree

from .theme import MARKER_PATTERN, Geometry

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}
A = f"{{{NS['a']}}}"
P = f"{{{NS['p']}}}"
R = f"{{{NS['r']}}}"
C = f"{{{NS['c']}}}"

URI_CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
URI_TABLE = "http://schemas.openxmlformats.org/drawingml/2006/table"
URI_DIAGRAM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
URI_OLE = "http://schemas.openxmlformats.org/presentationml/2006/ole"


def xp(el: etree._Element, path: str) -> list[Any]:
    """XPath с нашими префиксами: у элементов python-pptx свой ``xpath`` без ``namespaces``."""
    return etree._Element.xpath(el, path, namespaces=NS)


def localname(el: etree._Element) -> str:
    return etree.QName(el).localname


@dataclass
class ShapeEl:
    """Фигура слайда с абсолютной геометрией (с учётом групп) и признаком «внутри группы»."""

    el: etree._Element
    id: int
    name: str
    geometry: Geometry | None
    in_group: bool


def _xfrm(el: etree._Element) -> etree._Element | None:
    tag = localname(el)
    if tag == "graphicFrame":
        return el.find(f"{P}xfrm")
    if tag == "grpSp":
        return el.find(f"{P}grpSpPr/{A}xfrm")
    return el.find(f"{P}spPr/{A}xfrm")


def _box(xfrm: etree._Element | None) -> tuple[int, int, int, int] | None:
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
    if off is None or ext is None:
        return None
    return int(off.get("x", 0)), int(off.get("y", 0)), int(ext.get("cx", 0)), int(ext.get("cy", 0))


def iter_shapes(sp_tree: etree._Element) -> Iterator[ShapeEl]:
    """Все фигуры дерева, заходя в группы. Геометрия фигур в группе переводится в координаты
    слайда по ``chOff``/``chExt`` группы."""

    def walk(parent: etree._Element, tf: tuple[float, float, float, float], in_group: bool) -> Iterator[ShapeEl]:
        ox, oy, sx, sy = tf
        for el in parent:
            tag = localname(el)
            if tag not in ("sp", "grpSp", "graphicFrame", "cxnSp", "pic", "contentPart"):
                continue
            cnv = el.find(f".//{P}cNvPr")
            sid = int(cnv.get("id", 0)) if cnv is not None else 0
            name = cnv.get("name", "") if cnv is not None else ""
            box = _box(_xfrm(el))
            geom = None
            if box is not None:
                x, y, cx, cy = box
                geom = Geometry(x=int(ox + x * sx), y=int(oy + y * sy), cx=int(cx * sx), cy=int(cy * sy))
            if tag == "grpSp":
                yield ShapeEl(el, sid, name, geom, in_group)
                x_el = el.find(f"{P}grpSpPr/{A}xfrm")
                ch_off = x_el.find(f"{A}chOff") if x_el is not None else None
                ch_ext = x_el.find(f"{A}chExt") if x_el is not None else None
                if box is not None and ch_off is not None and ch_ext is not None:
                    x, y, cx, cy = box
                    cox, coy = int(ch_off.get("x", 0)), int(ch_off.get("y", 0))
                    ccx, ccy = int(ch_ext.get("cx", 0)) or 1, int(ch_ext.get("cy", 0)) or 1
                    nsx, nsy = sx * cx / ccx, sy * cy / ccy
                    child = (ox + (x - cox * cx / ccx) * sx, oy + (y - coy * cy / ccy) * sy, nsx, nsy)
                else:
                    child = tf
                yield from walk(el, child, True)
                continue
            yield ShapeEl(el, sid, name, geom, in_group)

    yield from walk(sp_tree, (0.0, 0.0, 1.0, 1.0), False)


def graphic_uri(el: etree._Element) -> str | None:
    gd = el.find(f"{A}graphic/{A}graphicData")
    return gd.get("uri") if gd is not None else None


# --- текст абзаца ------------------------------------------------------------------------


@dataclass
class Segment:
    """Кусок текста абзаца: прогон ``a:r``, поле ``a:fld`` или перенос ``a:br``."""

    el: etree._Element
    kind: str  # r, fld, br
    start: int
    text: str


def paragraph_segments(p: etree._Element) -> tuple[str, list[Segment]]:
    """Текст абзаца и его куски по порядку: PowerPoint режет текст на прогоны по языку и
    флагу орфографии, поэтому метка часто лежит в 3–6 прогонах."""
    segs: list[Segment] = []
    pos = 0
    for el in p:
        tag = localname(el)
        if tag in ("r", "fld"):
            t = el.find(f"{A}t")
            text = (t.text or "") if t is not None else ""
        elif tag == "br":
            text = "\n"
        else:
            continue
        segs.append(Segment(el, tag, pos, text))
        pos += len(text)
    return "".join(s.text for s in segs), segs


def text_frames(el: etree._Element) -> Iterator[tuple[etree._Element, tuple[int, int] | None]]:
    """Текстовые блоки фигуры: ``p:txBody`` надписи или ``a:txBody`` каждой ячейки таблицы."""
    body = el.find(f"{P}txBody")
    if body is not None:
        yield body, None
        return
    tbl = el.find(f"{A}graphic/{A}graphicData/{A}tbl")
    if tbl is None:
        return
    for i, tr in enumerate(tbl.findall(f"{A}tr")):
        for j, tc in enumerate(tr.findall(f"{A}tc")):
            tb = tc.find(f"{A}txBody")
            if tb is not None:
                yield tb, (i, j)


def all_text(el: etree._Element) -> str:
    """Текст фигуры: абзацы через перенос строки, разрыв строки внутри абзаца — тоже перенос."""
    return "\n".join(paragraph_segments(p)[0] for p in el.iter(f"{A}p"))


# --- метки ----------------------------------------------------------------------------

MARKER_RE = re.compile(MARKER_PATTERN)
LOOSE_RE = re.compile(r"\{\{([^{}]{1,64}?)\}\}")
"""Метка, которую нашёл бы человек, в том числе разрезанная переносом строки: такую заменить
нельзя, но показать в проверке шаблона нужно."""


@dataclass
class MarkerMatch:
    """Вхождение метки в тексте фигуры."""

    name: str
    occurrence: int
    body: etree._Element
    cell: tuple[int, int] | None
    paragraph: int
    p: etree._Element
    start: int
    end: int
    text: str
    segments: list[Segment]
    reason: str | None

    @property
    def covered(self) -> list[Segment]:
        return [s for s in self.segments if s.start < self.end and s.start + len(s.text) > self.start]


def iter_markers(el: etree._Element) -> Iterator[MarkerMatch]:
    """Метки фигуры по порядку: текстовые блоки, абзацы, вхождения слева направо. Номер
    вхождения считается по имени в пределах фигуры, с единицы."""
    seen: Counter[str] = Counter()
    for body, cell in text_frames(el):
        for pi, p in enumerate(body.findall(f"{A}p")):
            text, segs = paragraph_segments(p)
            if "{{" not in text:
                continue
            for m in LOOSE_RE.finditer(text):
                raw = m.group(1)
                name = raw.replace("\n", "").strip()
                if not name:
                    continue
                covered = [s for s in segs if s.start < m.end() and s.start + len(s.text) > m.start()]
                reason = None
                if "\n" in raw:
                    reason = "метку разрезает перенос строки"
                elif any(s.kind == "fld" for s in covered):
                    reason = "метка пересекает поле (номер слайда, дату)"
                elif not MARKER_RE.fullmatch(m.group(0)):
                    reason = "в имени метки недопустимые символы"
                seen[name] += 1
                yield MarkerMatch(name, seen[name], body, cell, pi, p, m.start(), m.end(), text, segs, reason)
