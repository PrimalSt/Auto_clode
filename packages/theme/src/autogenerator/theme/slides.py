"""Манифест слайдов-образцов: метки, графики и таблицы каждого слайда шаблона
(ARCHITECTURE.md, раздел 6.5)."""

from __future__ import annotations

import itertools
from typing import Any

from lxml import etree

from autogenerator.contracts import (
    ChartGroupInfo,
    ChartInfo,
    MarkerInfo,
    SeriesInfo,
    TableInfo,
    TemplateSlideInfo,
    TextStyle,
)
from autogenerator.contracts.ooxml import (
    URI_CHART,
    URI_TABLE,
    A,
    C,
    P,
    R,
    ShapeEl,
    graphic_uri,
    iter_markers,
    iter_shapes,
    localname,
    xp,
)
from autogenerator.contracts.theme import EMU_PER_INCH

AFTER = 12
AGEN_SLIDE = "AGEN_SLIDE"


# --- метки --------------------------------------------------------------------------------


def _style(run: etree._Element | None) -> TextStyle:
    if run is None:
        return TextStyle()
    rpr = run.find(f"{A}rPr")
    if rpr is None:
        return TextStyle()
    latin = rpr.find(f"{A}latin")
    sz = rpr.get("sz")
    b = rpr.get("b")
    return TextStyle(
        font=latin.get("typeface") if latin is not None else None,
        size=int(sz) / 100 if sz else None,
        bold=None if b is None else b in ("1", "true"),
        lang=rpr.get("lang"),
    )


def _body_props(body: etree._Element) -> tuple[bool, str | None]:
    bp = body.find(f"{A}bodyPr")
    if bp is None:
        return True, None
    wrap = bp.get("wrap") != "none"
    autofit = None
    if bp.find(f"{A}spAutoFit") is not None:
        autofit = "shape"
    elif bp.find(f"{A}normAutofit") is not None:
        autofit = "text"
    return wrap, autofit


def shape_markers(sh: ShapeEl) -> list[MarkerInfo]:
    """Метки в тексте фигуры (или ячеек таблицы) по порядку."""
    out: list[MarkerInfo] = []
    for m in iter_markers(sh.el):
        wrap, autofit = _body_props(m.body)
        ppr = m.p.find(f"{A}pPr")
        covered = m.covered
        first = covered[0].el if covered and covered[0].kind == "r" else None
        out.append(
            MarkerInfo(
                name=m.name,
                shape_id=sh.id,
                shape_name=sh.name,
                occurrence=m.occurrence,
                paragraph=m.paragraph,
                cell=m.cell,
                text_after=m.text[m.end : m.end + AFTER].split("\n")[0],
                runs=len([s for s in covered if s.kind != "br"]),
                style=_style(first),
                geometry=sh.geometry,
                wrap=wrap,
                align=ppr.get("algn") if ppr is not None else None,
                autofit=autofit,
                replaceable=m.reason is None,
                reason=m.reason,
            )
        )
    return out


# --- графики ------------------------------------------------------------------------------


def _val(el: etree._Element | None, tag: str) -> str | None:
    if el is None:
        return None
    e = el.find(f"{C}{tag}")
    return e.get("val") if e is not None else None


def _color(sppr: etree._Element | None) -> str | None:
    if sppr is None:
        return None
    for path in ("a:solidFill", "a:ln/a:solidFill"):
        fill = xp(sppr, f"./{path}/*")
        if fill:
            f = fill[0]
            if localname(f) == "srgbClr":
                return str(f.get("val")).upper()
            if localname(f) == "schemeClr":
                return f"scheme:{f.get('val')}"
    return None


def _series(ser: etree._Element) -> SeriesInfo:
    name = "".join(xp(ser, "./c:tx//c:v/text()"))
    fmt = xp(ser, "./c:val//c:formatCode/text()")
    pts = xp(ser, "./c:val//c:ptCount/@val")
    return SeriesInfo(
        idx=int(_val(ser, "idx") or 0),
        order=int(_val(ser, "order") or 0),
        name=name,
        color=_color(ser.find(f"{C}spPr")),
        number_format=fmt[0] if fmt else None,
        points=int(pts[0]) if pts else 0,
    )


def _labels(group: etree._Element) -> tuple[bool, str | None]:
    """Подписи данных группы: включены ли и с каким форматом (из группы или первой серии)."""
    shown = False
    fmt = None
    for dls in [group.find(f"{C}dLbls"), *xp(group, "./c:ser/c:dLbls")]:
        if dls is None:
            continue
        if _val(dls, "showVal") in ("1", "true") or _val(dls, "showPercent") in ("1", "true"):
            shown = True
        nf = dls.find(f"{C}numFmt")
        if nf is not None and fmt is None and nf.get("sourceLinked") not in ("1", "true"):
            fmt = nf.get("formatCode")
    return shown, fmt


def chart_groups(cs: etree._Element) -> list[ChartGroupInfo]:
    plot_area = cs.find(f"{C}chart/{C}plotArea")
    if plot_area is None:
        return []
    axes = {_val(a, "axId"): a for a in plot_area if localname(a).endswith("Ax")}
    groups: list[ChartGroupInfo] = []
    primary_val: str | None = None
    for g in plot_area:
        ln = localname(g)
        if not ln.endswith("Chart"):
            continue
        ax_ids = [a.get("val") for a in g.findall(f"{C}axId")]
        val_ax = next((a for a in ax_ids if a in axes and localname(axes[a]) == "valAx"), None)
        if primary_val is None:
            primary_val = val_ax
        shown, fmt = _labels(g)
        series = sorted((_series(s) for s in g.findall(f"{C}ser")), key=lambda s: s.order)
        groups.append(
            ChartGroupInfo(
                number=len(groups) + 1,
                kind=ln[: -len("Chart")],
                direction=_val(g, "barDir"),
                grouping=_val(g, "grouping"),
                secondary=val_ax is not None and val_ax != primary_val,
                series=series,
                label_format=fmt,
                labels=shown,
            )
        )
    return groups


def chart_info(sh: ShapeEl, slide_part: Any) -> ChartInfo | None:
    ref = sh.el.find(f"{A}graphic/{A}graphicData/{C}chart")
    if ref is None:
        return None
    try:
        part = slide_part.related_part(ref.get(f"{R}id"))
    except KeyError:
        return None
    cs = part._element
    try:
        chart_type = part.chart.chart_type.name
    except Exception:  # python-pptx не знает тип (например, график с областями и линией)
        chart_type = ""
    groups = chart_groups(cs)
    first = xp(cs, ".//c:plotArea/*[substring(local-name(), string-length(local-name()) - 4) = 'Chart']/c:ser[1]")
    cats = 0
    if first:
        cnt = xp(first[0], "./c:cat//c:ptCount/@val")
        cats = int(cnt[0]) if cnt else int((xp(first[0], "./c:val//c:ptCount/@val") or [0])[0])
    settings = len(xp(cs, ".//c:ser/c:dPt")) + len(xp(cs, ".//c:ser/c:dLbls/c:dLbl"))
    return ChartInfo(
        shape_id=sh.id,
        shape_name=sh.name,
        geometry=sh.geometry,
        chart_type=chart_type,
        groups=groups,
        categories=cats,
        point_settings=settings,
    )


def table_info(sh: ShapeEl) -> TableInfo | None:
    tbl = sh.el.find(f"{A}graphic/{A}graphicData/{A}tbl")
    if tbl is None:
        return None
    rows = tbl.findall(f"{A}tr")
    cols = tbl.findall(f"{A}tblGrid/{A}gridCol")
    pr = tbl.find(f"{A}tblPr")
    style = pr.find(f"{A}tableStyleId") if pr is not None else None
    header = ["".join(t.text or "" for t in tc.iter(f"{A}t")) for tc in rows[0].findall(f"{A}tc")] if rows else []
    return TableInfo(
        shape_id=sh.id,
        shape_name=sh.name,
        geometry=sh.geometry,
        rows=len(rows),
        cols=len(cols),
        style_id=style.text if style is not None else None,
        first_row=pr is not None and pr.get("firstRow") in ("1", "true"),
        header=header,
    )


# --- связи между графиками и надписями ----------------------------------------------------------


def _evenly_spaced(centers: list[float], n: int, span: float) -> bool:
    """Среди центров есть ``n`` идущих подряд через равные (±25 %) промежутки, и вместе они
    занимают не меньше половины графика."""
    tol = span / n / 4
    rows: list[float] = []
    for c in sorted(centers):
        if rows and c - rows[-1] <= tol:
            continue  # надписи одного ряда
        rows.append(c)
    for i in range(len(rows) - n + 1):
        run = rows[i : i + n]
        gaps = [b - a for a, b in itertools.pairwise(run)]
        mid = sorted(gaps)[len(gaps) // 2]
        if mid > 0 and all(abs(x - mid) <= 0.25 * mid for x in gaps) and run[-1] - run[0] >= span / 2 * (n - 1) / n:
            return True
    return False


def labels_per_category(chart: ChartInfo, markers: list[MarkerInfo]) -> bool:
    """Надписи с метками стоят напротив каждой категории графика (как у 100%-гистограмм с
    подписями справа от полос): тогда число и порядок категорий нельзя менять.

    Надписи на графике или рядом с концом полос (справа от горизонтальных полос, под
    столбцами) собираются в колонки по общему краю или центру; если в какой-то колонке
    столько же надписей через равные промежутки, сколько категорий, и они занимают не меньше
    половины графика, надписи расставлены по категориям. Карточки показателей над графиком
    не считаются."""
    g = chart.geometry
    n = chart.categories
    if g is None or n < 2 or not chart.groups:
        return False
    horizontal = chart.groups[0].direction == "bar"
    lo, hi = (g.y, g.bottom) if horizontal else (g.x, g.right)
    olo, ohi = (g.x, g.right + g.cx / 2) if horizontal else (g.y, g.bottom + g.cy / 4)
    boxes = []
    for m in markers:
        b = m.geometry
        if b is None or m.cell is not None:
            continue
        along = b.y + b.cy / 2 if horizontal else b.x + b.cx / 2
        edges = (b.x, b.x + b.cx / 2, b.right) if horizontal else (b.y, b.y + b.cy / 2, b.bottom)
        if lo <= along <= hi and olo <= edges[1] <= ohi:
            boxes.append((along, edges))
    align_tol = EMU_PER_INCH / 4
    for k in range(3):  # левый край, центр, правый край (у столбцов — верх, центр, низ)
        column: list[float] = []
        last: float | None = None
        for along, edges in sorted(boxes, key=lambda b: b[1][k]):
            if last is not None and edges[k] - last > align_tol:
                if _evenly_spaced(column, n, hi - lo):
                    return True
                column = []
            column.append(along)
            last = edges[k]
        if _evenly_spaced(column, n, hi - lo):
            return True
    return False


def link_overlaid(charts: list[ChartInfo]) -> None:
    """Графики, наложенные друг на друга (рамки совпадают больше чем наполовину)."""
    for a in charts:
        for b in charts:
            if a is b or a.geometry is None or b.geometry is None:
                continue
            smaller = min(a.geometry.cx * a.geometry.cy, b.geometry.cx * b.geometry.cy)
            if smaller and a.geometry.intersection(b.geometry) > smaller / 2:
                a.overlaid.append(b.shape_id)


# --- слайд --------------------------------------------------------------------------------


def _agen_tag(slide: Any) -> str | None:
    """Значение тега AGEN_SLIDE слайда, выгруженного приложением (F-422, v1)."""
    for t in xp(slide._element, "./p:cSld/p:custDataLst/p:tags"):
        try:
            part = slide.part.related_part(t.get(f"{R}id"))
        except KeyError:
            continue
        root = etree.fromstring(part.blob)
        for tag in root.iter(f"{P}tag"):
            if tag.get("name") == AGEN_SLIDE:
                return tag.get("val")
    return None


def slide_info(slide: Any, number: int, slide_id: int, layout_key: str) -> TemplateSlideInfo:
    markers: list[MarkerInfo] = []
    charts: list[ChartInfo] = []
    tables: list[TableInfo] = []
    tree = slide._element.find(f"{P}cSld/{P}spTree")
    for sh in iter_shapes(tree):
        markers += shape_markers(sh)
        if localname(sh.el) != "graphicFrame":
            continue
        uri = graphic_uri(sh.el)
        if uri == URI_CHART:
            info = chart_info(sh, slide.part)
            if info is not None:
                charts.append(info)
        elif uri == URI_TABLE:
            t = table_info(sh)
            if t is not None:
                tables.append(t)
    for c in charts:
        c.labels_per_category = labels_per_category(c, markers)
    link_overlaid(charts)
    title = None
    if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
        title = slide.shapes.title.text_frame.text or None
    if title is None:
        # Заголовок слайда-образца часто — обычная надпись вверху слайда: берём самую верхнюю.
        tops = [
            (sh.geometry.y, sh)
            for sh in iter_shapes(tree)
            if sh.geometry is not None and sh.el.find(f"{P}txBody") is not None and not sh.in_group
        ]
        for _, sh in sorted(tops, key=lambda t: t[0]):
            text = "".join(t.text or "" for t in sh.el.iter(f"{A}t")).strip()
            if text:
                title = text
                break
    return TemplateSlideInfo(
        slide_id=slide_id,
        number=number,
        layout_key=layout_key,
        layout_name=slide.slide_layout.name,
        title=title,
        markers=markers,
        charts=charts,
        tables=tables,
        agen_tag=_agen_tag(slide),
    )
