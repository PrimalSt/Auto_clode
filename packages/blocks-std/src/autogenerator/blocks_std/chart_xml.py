"""Правка XML графиков слайдов-образцов, которой нет в python-pptx (ARCHITECTURE.md, раздел 6.5).

- Число серий по группам. ``replace_data()`` python-pptx ломает комбинированный график, когда
  число серий меняется: лишняя серия попадает в группу линии, а при удалении пропадает вся
  группа линии. Поэтому серии добавляются и удаляются здесь, внутри своей группы: новая серия —
  копия последней в группе с ``c:idx`` и ``c:order``, не занятыми во всём графике, новым
  ``c16:uniqueId`` и цветом, отличным от остальных серий; группа никогда не остаётся пустой.
- Настройки точек и подписи точек, для которых нет категории, удаляются; у круговой
  диаграммы с большим числом секторов настройки достраиваются.
- Формат чисел серии, заданный в сценарии, получают и её подписи: у подписей шаблона обычно
  свой формат, не связанный с данными.
- Проверка готового графика: оси групп, уникальные ``c:idx`` и ``c16:uniqueId``, число точек,
  положение подписи «снаружи» у столбцов с накоплением (PowerPoint такой файл не открывает).
"""

from __future__ import annotations

import colorsys
import copy
import uuid
from typing import Any

from lxml import etree

from .chart_layout import NS, A, C, ln, val, x1, xp

C16 = "http://schemas.microsoft.com/office/drawing/2014/chart"
UNIQUE_ID_URI = "{C3380CC4-5D6E-409C-BE32-E72D297353CC}"
FILLS = ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")
FALLBACK = ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47", "264478", "9E480E", "636363", "997300"]
MIN_DISTANCE = 60.0
SCHEME_ALIASES = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


def plot_area(cs: Any) -> Any:
    return cs.find(f"{C}chart/{C}plotArea")


def groups(cs: Any) -> list[Any]:
    return [g for g in plot_area(cs) if ln(g).endswith("Chart")]


def group_series(g: Any) -> list[Any]:
    """Серии группы в порядке ``c:order`` — так их перебирает ``replace_data()``."""
    return sorted(g.findall(f"{C}ser"), key=lambda s: int(val(s, "order", "0")))


# --- цвета -----------------------------------------------------------------------------------


def theme_colors(slide: Any) -> dict[str, str]:
    """Цвета темы слайда: ``accent1`` → ``"1F4E79"`` и т. д."""
    try:
        master = slide.slide_layout.slide_master
        part = next(r.target_part for r in master.part.rels.values() if r.reltype.endswith("/theme"))
    except (AttributeError, StopIteration):
        return {}
    root = etree.fromstring(part.blob)
    scheme = root.find(f".//{A}clrScheme")
    out: dict[str, str] = {}
    if scheme is None:
        return out
    for el in scheme:
        child = el[0] if len(el) else None
        if child is None:
            continue
        rgb = child.get("val") if ln(child) == "srgbClr" else child.get("lastClr")
        if rgb:
            out[ln(el)] = rgb.upper()
    return out


def _apply_mods(rgb: str, color_el: Any) -> str:
    r, g, b = (int(rgb[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    for mod in color_el:
        v = int(mod.get("val", "100000")) / 100000
        name = ln(mod)
        if name == "lumMod":
            lum *= v
        elif name == "lumOff":
            lum += v
        elif name == "tint":
            lum = lum * v + (1 - v)
        elif name == "shade":
            lum *= v
    lum = min(max(lum, 0.0), 1.0)
    r, g, b = colorsys.hls_to_rgb(h, lum, s)
    return f"{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


def resolve_color(color_el: Any, theme: dict[str, str]) -> str | None:
    """``a:srgbClr``/``a:schemeClr`` с оттенками → RGB."""
    name = ln(color_el)
    if name == "srgbClr":
        base = color_el.get("val")
    elif name == "schemeClr":
        key = color_el.get("val")
        base = theme.get(SCHEME_ALIASES.get(key, key))
    elif name == "sysClr":
        base = color_el.get("lastClr")
    else:
        return None
    return _apply_mods(base.upper(), color_el) if base else None


def series_color(ser: Any, theme: dict[str, str]) -> str | None:
    sppr = ser.find(f"{C}spPr")
    if sppr is None:
        return None
    for path in ("./a:solidFill/*", "./a:ln/a:solidFill/*"):
        found = xp(sppr, path)
        if found:
            return resolve_color(found[0], theme)
    return None


def _auto_color(ser: Any, theme: dict[str, str]) -> str | None:
    """Цвет серии без своей заливки: в стиле по умолчанию PowerPoint берёт accent1…6 по ``c:idx``."""
    return theme.get(f"accent{int(val(ser, 'idx', '0')) % 6 + 1}")


def _distance(a: str, b: str) -> float:
    pa = [int(a[i : i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b[i : i + 2], 16) for i in (0, 2, 4)]
    return float(sum((x - y) ** 2 for x, y in zip(pa, pb, strict=True)) ** 0.5)


def distinct_color(used: list[str], theme: dict[str, str]) -> str:
    """Цвет, заметно отличный от уже занятых: цвета темы, их светлые и тёмные варианты, запасная палитра."""
    accents = [theme[f"accent{i}"] for i in range(1, 7) if f"accent{i}" in theme]
    lighter = [_apply_mods(c, etree.fromstring(_mods_xml(60000, 40000))) for c in accents]
    darker = [_apply_mods(c, etree.fromstring(_mods_xml(75000, 0))) for c in accents]
    for c in [*accents, *lighter, *darker, *FALLBACK]:
        if all(_distance(c, u) >= MIN_DISTANCE for u in used):
            return c
    return FALLBACK[len(used) % len(FALLBACK)]


def _mods_xml(lum_mod: int, lum_off: int) -> str:
    off = f'<a:lumOff val="{lum_off}"/>' if lum_off else ""
    return f'<a:x xmlns:a="{NS["a"]}"><a:lumMod val="{lum_mod}"/>{off}</a:x>'


def _set_fill(sppr: Any, rgb: str) -> None:
    for tag in FILLS:
        for e in sppr.findall(f"{A}{tag}"):
            sppr.remove(e)
    fill = etree.Element(f"{A}solidFill")
    etree.SubElement(fill, f"{A}srgbClr", val=rgb)
    # spPr: xfrm?, геометрия?, заливка?, ln?, …
    anchor = next(
        (
            sppr.find(f"{A}{t}")
            for t in ("ln", "effectLst", "effectDag", "scene3d", "sp3d", "extLst")
            if sppr.find(f"{A}{t}") is not None
        ),
        None,
    )
    if anchor is not None:
        anchor.addprevious(fill)
    else:
        sppr.append(fill)


def set_series_color(ser: Any, rgb: str, line: bool) -> None:
    sppr = ser.find(f"{C}spPr")
    if sppr is None:
        sppr = etree.Element(f"{C}spPr")
        anchor = next((ser.find(f"{C}{t}") for t in ("tx",) if ser.find(f"{C}{t}") is not None), None)
        if anchor is not None:
            anchor.addnext(sppr)
        else:
            ser.find(f"{C}order").addnext(sppr)
    if line:
        lnel = sppr.find(f"{A}ln")
        if lnel is None:
            lnel = etree.SubElement(sppr, f"{A}ln")
        _set_fill(lnel, rgb)
        msp = x1(ser, "./c:marker/c:spPr")
        if msp is not None:
            _set_fill(msp, rgb)
            mln = msp.find(f"{A}ln")
            if mln is not None:
                _set_fill(mln, rgb)
    else:
        _set_fill(sppr, rgb)


# --- число серий -------------------------------------------------------------------------------


def _new_unique_id(ser: Any) -> None:
    for uid in ser.iter(f"{{{C16}}}uniqueId"):
        uid.set("val", "{" + str(uuid.uuid4()).upper() + "}")


def set_series_counts(cs: Any, counts: list[int], theme: dict[str, str]) -> None:
    """Сделать в группах графика ``counts`` серий (по группам в порядке документа)."""
    gs = groups(cs)
    if len(counts) != len(gs):
        raise ValueError(f"в графике {len(gs)} групп серий, а задано {len(counts)}")
    all_sers = list(plot_area(cs).iter(f"{C}ser"))
    next_idx = max([int(val(s, "idx", "0")) for s in all_sers] + [-1]) + 1
    next_order = max([int(val(s, "order", "0")) for s in all_sers] + [-1]) + 1
    # Цвет серии без своей заливки тоже занят: иначе новая серия может получить его же.
    used = [c for c in (series_color(s, theme) or _auto_color(s, theme) for s in all_sers) if c]
    for g, want in zip(gs, counts, strict=True):
        if want < 1:
            raise ValueError("у группы серий графика должна остаться хотя бы одна серия")
        sers = group_series(g)
        for extra in sers[want:]:
            g.remove(extra)
        last = sers[min(want, len(sers)) - 1]
        line = ln(g) in ("lineChart", "radarChart", "scatterChart")
        for _ in range(want - len(sers)):
            new = copy.deepcopy(last)
            new.find(f"{C}idx").set("val", str(next_idx))
            new.find(f"{C}order").set("val", str(next_order))
            next_idx += 1
            next_order += 1
            _new_unique_id(new)
            for dpt in new.findall(f"{C}dPt"):
                new.remove(dpt)  # цвета отдельных точек — у серии-образца, не у новой
            color = distinct_color(used, theme)
            used.append(color)
            set_series_color(new, color, line)
            last.addnext(new)
            last = new


# --- точки -------------------------------------------------------------------------------------


def cleanup_points(cs: Any, n: int, theme: dict[str, str]) -> None:
    """Удалить настройки и подписи точек без категории; у круговых — достроить недостающие."""
    for ser in plot_area(cs).iter(f"{C}ser"):
        for dpt in ser.findall(f"{C}dPt"):
            if int(val(dpt, "idx", "0")) >= n:
                ser.remove(dpt)
        dls = ser.find(f"{C}dLbls")
        if dls is not None:
            for d in dls.findall(f"{C}dLbl"):
                if int(val(d, "idx", "0")) >= n:
                    dls.remove(d)
    for g in groups(cs):
        if ln(g) not in ("pieChart", "pie3DChart", "doughnutChart", "ofPieChart"):
            continue
        for ser in g.findall(f"{C}ser"):
            dpts = ser.findall(f"{C}dPt")
            if not dpts:
                continue
            have = {int(val(d, "idx", "0")) for d in dpts}
            used = [c for c in (_point_color(d, theme) for d in dpts) if c]
            for i in range(n):
                if i in have:
                    continue
                new = copy.deepcopy(dpts[i % len(dpts)])
                new.find(f"{C}idx").set("val", str(i))
                _new_unique_id(new)  # другая точка — другой c16:uniqueId
                sppr = new.find(f"{C}spPr")
                if sppr is not None:
                    color = distinct_color(used, theme)
                    used.append(color)
                    _set_fill(sppr, color)
                current = ser.findall(f"{C}dPt")
                prev = [d for d in current if int(val(d, "idx", "0")) < i]
                if prev:
                    prev[-1].addnext(new)
                else:
                    current[0].addprevious(new)


def _point_color(dpt: Any, theme: dict[str, str]) -> str | None:
    found = xp(dpt, "./c:spPr/a:solidFill/*")
    return resolve_color(found[0], theme) if found else None


# --- формат подписей ----------------------------------------------------------------------------


def set_label_formats(cs: Any, formats: list[str | None]) -> None:
    """Формат подписей серий (по группам в порядке документа, внутри группы — по ``c:order``);
    ``None`` — оставить как в шаблоне. Меняются подписи самой серии; общие подписи группы — если
    у всех серий группы задан один и тот же формат."""
    k = 0
    for g in groups(cs):
        sers = group_series(g)
        fmts = formats[k : k + len(sers)]
        k += len(sers)
        for ser, fmt in zip(sers, fmts, strict=False):
            dls = ser.find(f"{C}dLbls")
            if fmt is None or dls is None or dls.find(f"{C}delete") is not None:
                continue
            _set_num_fmt(dls, fmt, after=dls.findall(f"{C}dLbl"))
            for d in dls.findall(f"{C}dLbl"):
                nf = d.find(f"{C}numFmt")
                if nf is not None:
                    nf.set("formatCode", fmt)
                    nf.set("sourceLinked", "0")
        common = g.find(f"{C}dLbls")
        same = len(fmts) == len(sers) and fmts[0] is not None and len(set(fmts)) == 1
        if common is not None and common.find(f"{C}delete") is None and same:
            _set_num_fmt(common, str(fmts[0]), after=common.findall(f"{C}dLbl"))


def _set_num_fmt(parent: Any, fmt: str, after: list[Any]) -> None:
    nf = parent.find(f"{C}numFmt")
    if nf is None:
        nf = etree.Element(f"{C}numFmt")
        if after:
            after[-1].addnext(nf)
        else:
            parent.insert(0, nf)
    nf.set("formatCode", fmt)
    nf.set("sourceLinked", "0")


# --- проверка ----------------------------------------------------------------------------------


def _uids(el: Any) -> list[str]:
    """c16:uniqueId самого элемента (серии, точки, подписи), без вложенных."""
    return [u.get("val") for u in el.findall(f"{C}extLst/{C}ext/{{{C16}}}uniqueId")]


def check_chart(cs: Any, categories: int) -> list[str]:
    """Проверка готового графика: то, что PowerPoint не прощает или что выдаёт ошибку заполнения."""
    problems: list[str] = []
    pa = plot_area(cs)
    axes = {val(a, "axId"): a for a in pa if ln(a).endswith("Ax")}
    used: set[str] = set()
    for g in groups(cs):
        ids = [a.get("val") for a in g.findall(f"{C}axId")]
        if ln(g) not in ("pieChart", "pie3DChart", "doughnutChart", "ofPieChart"):
            missing = [a for a in ids if a not in axes]
            if missing or not ids:
                problems.append(f"у группы {ln(g)} нет своих осей")
        used.update(ids)
        if not g.findall(f"{C}ser"):
            problems.append(f"группа {ln(g)} осталась без серий")
        stacked = val(g, "grouping") in ("stacked", "percentStacked")
        if ln(g) == "barChart" and stacked and xp(g, ".//c:dLblPos[@val='outEnd']"):
            problems.append("у столбцов с накоплением подписи «снаружи»: PowerPoint не откроет такой файл")
    for aid in axes:
        if aid not in used:
            problems.append(f"ось {aid} не используется ни одной группой")
    sers = list(pa.iter(f"{C}ser"))
    idx = [val(s, "idx") for s in sers]
    if len(set(idx)) != len(idx):
        problems.append("у серий повторяются c:idx")
    # У серии свой c16:uniqueId; у точки (c:dPt) и её подписи (c:dLbl) он общий — так их
    # связывает PowerPoint, — но у разных точек серии разный.
    uids = [u for s in sers for u in _uids(s)]
    if len(set(uids)) != len(uids):
        problems.append("у серий повторяются c16:uniqueId")
    for s in sers:
        for kind in ("dPt", "dLbls/c:dLbl"):
            pts = [u for el in xp(s, f"./c:{kind}") for u in _uids(el)]
            if len(set(pts)) != len(pts):
                problems.append(f"в серии {val(s, 'idx')} у разных точек одинаковые c16:uniqueId")
                break
    for s in sers:
        cnt = x1(s, "./c:val//c:ptCount/@val")
        if cnt is not None and int(cnt) != categories:
            problems.append(f"в серии {val(s, 'idx')} {cnt} точек, а категорий {categories}")
        for d in xp(s, "./c:dPt/c:idx/@val | ./c:dLbls/c:dLbl/c:idx/@val"):
            if int(d) >= categories:
                problems.append(f"в серии {val(s, 'idx')} настройка точки {d} без категории")
    return problems
