"""Элементы оформления ролей (ARCHITECTURE.md, раздел 6.5).

Декоративные фигуры, которых нет на макете роли, но есть на её слайде в шаблоне (логотип и
волна обложки), записываются как элементы оформления: ``render`` переносит их на новые слайды
этой роли. Если такая же фигура есть на макете с ``preserve="1"`` (в шаблоне пользователя
группы логотипа и волны обложки совпадают с группами макета «Последний»), она берётся с
макета, а положение — со слайда: так элемент не пропадёт, если слайд удалят из шаблона.

Элементы ищутся только у титульной, разделительной и финальной ролей: у слайдов с
содержанием картинки и фигуры — это содержание, а не оформление.
"""

from __future__ import annotations

import hashlib
from typing import Any

from lxml import etree

from autogenerator.contracts import DesignElement, Geometry, LayoutRole, RoleBinding
from autogenerator.contracts.ooxml import MARKER_RE, A, P, R

DECORATED_ROLES = (LayoutRole.TITLE, LayoutRole.SECTION, LayoutRole.FINAL)
KINDS = {f"{P}pic": "picture", f"{P}grpSp": "group", f"{P}sp": "shape", f"{P}cxnSp": "shape"}


def _texts(el: etree._Element) -> list[str]:
    return ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in el.iter(f"{A}p")]


def _kind(el: etree._Element) -> str | None:
    """Вид фигуры, если она может быть элементом оформления: картинка, группа, фигура без
    текста или линия. Плейсхолдеры, надписи с текстом, графики, таблицы и фигуры с метками —
    нет."""
    kind = KINDS.get(el.tag)
    if kind is None:
        return None
    if el.find(f".//{P}nvPr/{P}ph") is not None:
        return None
    text = " ".join(_texts(el)).strip()
    if MARKER_RE.search(text) or el.find(f".//{P}graphicFrame") is not None:
        return None
    if el.tag == f"{P}sp" and text:
        return None
    return kind


def _blob_hash(part: Any, rid: str) -> str:
    rel = part.rels.get(rid)
    if rel is None:
        return rid
    if rel.is_external:
        return rel.target_ref
    return hashlib.sha1(rel.target_part.blob).hexdigest()[:16]


def signature(el: etree._Element, part: Any) -> str:
    """Отпечаток фигуры без учёта положения, id и имён: вид, состав, размер, текст, цвета,
    формы и картинки. Одинаковый логотип на слайде и на макете даёт один отпечаток."""
    items: list[str] = [el.tag]
    xfrm = el.find(f"{P}grpSpPr/{A}xfrm") if el.tag == f"{P}grpSp" else el.find(f".//{A}xfrm")
    ext = xfrm.find(f"{A}ext") if xfrm is not None else None
    if ext is not None:
        items.append(f"{round(int(ext.get('cx', 0)) / 12700)}x{round(int(ext.get('cy', 0)) / 12700)}")
    for d in el.iter():
        if not isinstance(d.tag, str):
            continue
        local = d.tag.rsplit("}", 1)[-1]
        if local in ("srgbClr", "schemeClr", "prstGeom"):
            items.append(f"{local}={d.get('val') or d.get('prst')}")
        elif local in ("sp", "pic", "grpSp", "cxnSp"):
            items.append(local)
        for attr in (f"{R}embed", f"{R}link"):
            rid = d.get(attr)
            if rid:
                items.append(_blob_hash(part, rid))
    items += [t for t in _texts(el) if t.strip()]
    return hashlib.sha1("\n".join(items).encode()).hexdigest()


def _geometry(el: etree._Element) -> Geometry | None:
    xfrm = el.find(f"{P}grpSpPr/{A}xfrm") if el.tag == f"{P}grpSp" else el.find(f"{P}spPr/{A}xfrm")
    if xfrm is None:
        return None
    off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
    if off is None or ext is None:
        return None
    return Geometry(x=int(off.get("x", 0)), y=int(off.get("y", 0)), cx=int(ext.get("cx", 0)), cy=int(ext.get("cy", 0)))


def _shapes(tree: etree._Element) -> list[etree._Element]:
    return [el for el in tree if el.tag in KINDS]


def _name(el: etree._Element) -> tuple[int, str]:
    c = el.find(f".//{P}cNvPr")
    return (int(c.get("id", 0)), c.get("name", "")) if c is not None else (0, "")


def find_decorations(
    prs: Any, roles: list[RoleBinding], layouts: dict[str, Any], slides: list[tuple[int, Any, str]]
) -> list[RoleBinding]:
    """Роли с элементами оформления. ``layouts`` — ключ → макет python-pptx, ``slides`` —
    (sldId, слайд, ключ макета) по порядку."""
    # Фигуры макетов с preserve="1": отпечаток → (ключ макета, фигура).
    preserved: dict[str, tuple[str, etree._Element]] = {}
    for key, layout in layouts.items():
        if layout._element.get("preserve") != "1":
            continue
        for el in _shapes(layout.shapes._spTree):
            if _kind(el) is not None:
                preserved.setdefault(signature(el, layout.part), (key, el))
    out: list[RoleBinding] = []
    for b in roles:
        if b.role not in DECORATED_ROLES:
            out.append(b)
            continue
        found = next(((sid, s) for sid, s, key in slides if key == b.layout_key), None)
        layout = layouts.get(b.layout_key)
        if found is None or layout is None:
            out.append(b)
            continue
        sid, slide = found
        # То, что уже есть на макете роли или его мастере, на новом слайде появится само.
        inherited = {
            signature(el, part.part)
            for part in (layout, layout.slide_master)
            for el in _shapes(part.shapes._spTree)
            if _kind(el) is not None
        }
        items: list[DesignElement] = []
        for el in _shapes(slide.shapes._spTree):
            kind = _kind(el)
            geo = _geometry(el)
            if kind is None or geo is None:
                continue
            sig = signature(el, slide.part)
            if sig in inherited:
                continue
            shape_id, name = _name(el)
            src = preserved.get(sig)
            if src is not None and src[0] != b.layout_key:
                lid, lname = _name(src[1])
                items.append(
                    DesignElement(name=lname or name, kind=kind, shape_id=lid, from_layout=src[0], geometry=geo)
                )
            else:
                items.append(DesignElement(name=name, kind=kind, shape_id=shape_id, from_slide=sid, geometry=geo))
        out.append(b.model_copy(update={"decorations": items}) if items else b)
    return out
