"""Подписи комбинированных графиков: подписи линии на своей оси уводятся с подписей столбцов
(шаг ``chart_fill``, ARCHITECTURE.md, раздел 6.5; перенесено из прототипа).

Оси столбцов и линии масштабируются независимо, поэтому при других данных линия заходит в
верхние сегменты столбцов и её подписи ложатся на подписи столбцов. Правило «полос» для
каждой группы наложенных графиков слайда:

- участвуют линия с подписями на своей оси значений и вертикальные столбцы с подписями,
  область построения которых по высоте совпадает с линией хотя бы наполовину (тот же
  график — комбинированный — или другой график группы);
- если при текущих границах осей ни подпись, ни маркер линии не подходят к подписи столбца
  ближе ``NOOP_GAP``, ничего не меняется;
- столбцы получают нижнюю полосу: явные ``c:min``/``c:max`` оси столбцов, чтобы самая высокая
  стопка кончалась на доле ``b`` высоты области построения. ``b`` начинается с собственной
  доли шаблона и уменьшается шагами по 0,01, пока линия не поместится над столбцами, но не
  ниже 45 % (в крайнем случае 30 %); выше, чем были, столбцы не становятся;
- препятствия считаются по колонкам: столбцы и их подписи всех графиков группы под точкой,
  подписью или отрезком линии. Точка должна быть не ниже ``clearance`` (0,08 дюйма, плюс 0,04,
  когда область построения только оценена) над ними, её подпись (над точкой, ``c:dLblPos t``)
  должна оставаться внутри области построения;
- ось линии получает явные границы; размах линии близок к шаблонному (штраф
  0,5 × |ln(новый/старый размах)|, поощрение за запас до 0,10 дюйма);
- график только со столбцами, наложенный на участника с теми же категориями (нижний график
  пары), получает ту же долю, чтобы стопки пары были одной высоты, — если от этого подписи его
  столбцов не налезут друг на друга там, где не налезали;
- скрытые оси (удалённые, без подписей, с белым или прозрачным текстом) получают точные
  границы, видимые — круглые (1/2/2,5/5 × 10^k, не больше 10 делений);
- ручные сдвиги подписей точек (подогнанные под демо-данные шаблона) снимаются;
- форматы чисел, цвета, шрифты, видимость осей и сетка не меняются;
- пропускаются с причиной: горизонтальные и 100%-столбцы, логарифмические и перевёрнутые оси,
  линии с накоплением, линия на одной оси со столбцами, другие виды групп.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from lxml import etree

from .chart_layout import (
    DEFAULT_SZ,
    LBL_GAP,
    C,
    Chart,
    chart_frames,
    chart_labels,
    find_pairs,
    inter,
    ln,
    num_cache,
    val,
    x1,
    xp,
)

CLEARANCE = 0.08  # дюйма от низа маркера до самого высокого препятствия в его колонке
EST_EXTRA = 0.04  # добавка, когда область построения оценена (нет manualLayout)
SEG_CLR = 0.03  # отрезок линии над столбцами и подписями между точками
TOP_PAD = 0.02  # верх подписи линии — верх области построения
NOOP_GAP = 0.08  # ближе этого подпись или маркер линии к подписи столбца — конфликт
B_START_MAX = 0.95
B_MIN, B_FLOOR, B_STEP = 0.45, 0.30, 0.01
WANT_CAP = 0.20  # желаемый размах линии при выборе b: min(шаблон, 0,2)
MIN_BAND = 0.12  # дюйма: меньше этого хода линия не получает, пока b не ниже B_MIN
SLACK_WANT = 0.04  # запас сверх clearance на уровне «want»
PEN_SHAPE, BONUS_SLACK = 0.5, 0.3
PAIR_MARGIN = 0.05  # запас при подсчёте наложений подписей столбцов в паре
HIDDEN_COLOURS = {"bg1", "lt1", "ffffff"}
FLAGS = ("showLegendKey", "showVal", "showCatName", "showSerName", "showPercent", "showBubbleSize")
AFTER_POS = (*FLAGS, "separator", "showLeaderLines", "leaderLines", "extLst")
Box = tuple[float, float, float, float]


def _flag(el: Any, tag: str) -> bool | None:
    v = val(el, tag)
    return None if v is None else v in ("1", "true")


def _num(v: float) -> float:
    return float(f"{v:.6g}")


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def _axis_hidden(ax: Any) -> bool:
    if val(ax, "delete", "0") in ("1", "true") or val(ax, "tickLblPos", "nextTo") == "none":
        return True
    fill = xp(ax, "./c:txPr/a:p/a:pPr/a:defRPr/a:solidFill/*")
    if fill:
        f = fill[0]
        if (f.get("val") or "").lower() in HIDDEN_COLOURS and len(f) == 0:
            return True
    return bool(xp(ax, "./c:txPr/a:p/a:pPr/a:defRPr/a:noFill"))


def _pa(cs: Any) -> Any:
    return cs.find(C + "chart").find(C + "plotArea")


def _axes(cs: Any) -> dict[str, Any]:
    return {val(a, "axId"): a for a in _pa(cs) if ln(a).endswith("Ax")}


def _groups(cs: Any) -> list[Any]:
    return [g for g in _pa(cs) if ln(g).endswith("Chart")]


def _vax(g: Any, axes: dict[str, Any]) -> str | None:
    return next((a for a in xp(g, "./c:axId/@val") if a in axes and ln(axes[a]) == "valAx"), None)


def _sname(ser: Any) -> str:
    return "".join(xp(ser, "./c:tx//c:v/text()")) or f"series {val(ser, 'idx')}"


def _copy(el: Any) -> Any:
    return etree.fromstring(etree.tostring(el))


# --- правка XML --------------------------------------------------------------------------------


def _set_scaling(ax: Any, mn: float, mx: float) -> tuple[Any, Any]:
    sc = ax.find(C + "scaling")
    was = (val(sc, "min"), val(sc, "max"))
    for t in ("max", "min"):
        e = sc.find(C + t)
        if e is not None:
            sc.remove(e)
    anchor = sc.find(C + "orientation")
    if anchor is None:
        anchor = sc.find(C + "logBase")
    e_max = etree.Element(C + "max")
    e_max.set("val", repr(mx))
    e_min = etree.Element(C + "min")
    e_min.set("val", repr(mn))
    if anchor is not None:
        anchor.addnext(e_max)
    else:
        sc.insert(0, e_max)
    e_max.addnext(e_min)  # схема: logBase?, orientation?, max?, min?, extLst?
    return was


def _set_major(ax: Any, step: float) -> None:
    """``c:majorUnit`` у видимой оси с круглыми границами: любая программа подпишет те же деления."""
    e = ax.find(C + "majorUnit")
    if e is None:
        e = etree.Element(C + "majorUnit")
        prev = next(
            (ax.find(C + t) for t in ("crossBetween", "crossesAt", "crosses", "crossAx") if ax.find(C + t) is not None),
            None,
        )
        if prev is None:
            return
        prev.addnext(e)  # valAx: … crossAx, (crosses|crossesAt)?, crossBetween?, majorUnit?, minorUnit?
    e.set("val", repr(step))


def _hidden_axis_numfmt(cs: Any, axes: dict[str, Any], aid: str, ax: Any) -> str | None:
    """С точными границами подписи формата General длинные (-0.0934296): на скрытой оси их не
    видно, но место они занимают в программах, которые не смотрят на sourceLinked. Ось
    получает формат линии, чтобы везде оставалось столько же места, сколько в PowerPoint."""
    fmt = next(
        (
            f
            for g in _groups(cs)
            if ln(g) == "lineChart" and _vax(g, axes) == aid
            for f in xp(g, "./c:ser/c:val//c:formatCode/text()")
            if f and f != "General"
        ),
        None,
    )
    nf = ax.find(C + "numFmt")
    if fmt is None or nf is None:
        return None
    nf.set("formatCode", fmt)
    nf.set("sourceLinked", "0")
    return str(fmt)


def _set_dlblpos(dl: Any, pos: str) -> Any:
    e = dl.find(C + "dLblPos")
    was = e.get("val") if e is not None else None
    if e is None:
        e = etree.Element(C + "dLblPos")
        nxt = next((dl.find(C + t) for t in AFTER_POS if dl.find(C + t) is not None), None)
        if nxt is not None:
            nxt.addprevious(e)
        else:
            dl.append(e)
    e.set("val", pos)
    return was


def _c14n(el: Any) -> bytes | None:
    return None if el is None else etree.tostring(el, method="c14n")


def _drop_point_layouts(ser: Any, sd: Any) -> list[dict[str, Any]]:
    """Снять ``c:layout`` с подписей точек; подпись точки, которая после этого только повторяет
    настройки серии, удаляется целиком."""
    out = []
    for p in list(sd.findall(C + "dLbl")):
        lay = p.find(C + "layout")
        if lay is None or lay.find(C + "manualLayout") is None:
            continue
        p.remove(lay)
        kids = {ln(k) for k in p}
        ext_content = xp(p, "./c:extLst/c:ext/*[local-name()!='uniqueId']")
        same = (
            all(val(p, k) in (None, val(sd, k)) for k in FLAGS)
            and all(
                p.find(C + t) is None or _c14n(p.find(C + t)) == _c14n(sd.find(C + t))
                for t in ("numFmt", "spPr", "txPr")
            )
            and (p.find(C + "dLblPos") is None or val(p, "dLblPos") == val(sd, "dLblPos"))
        )
        redundant = (
            kids <= {"idx", "extLst", "numFmt", "spPr", "txPr", "dLblPos", *FLAGS}
            and not ext_content
            and same
            and not _flag(p, "delete")
        )
        if redundant:
            sd.remove(p)
        out.append({"series": _sname(ser), "idx": int(val(p, "idx")), "dLbl_removed": bool(redundant)})
    return out


# --- масштаб -------------------------------------------------------------------------------------


def _nice_steps(span: float) -> list[float]:
    if span <= 0 or not math.isfinite(span):
        return [1.0]
    k = math.floor(math.log10(span / 10.0))
    return [m * 10.0**e for e in (k - 1, k, k + 1, k + 2) for m in (1, 2, 2.5, 5)]


def _bar_scale(
    lo: float, hi: float, b: float, visible: bool, major: float | None = None
) -> tuple[float, float, float | None]:
    """(min, max), чтобы [lo, hi] (lo ≤ 0 ≤ hi) занимал нижнюю долю ``b`` области построения
    (с круглыми границами, если ось видна)."""
    if lo == 0 and hi == 0:
        return 0.0, 1.0, None
    if not visible:
        mn = lo * 1.05 if lo < 0 else 0.0
        return _num(mn), _num(mn + (hi - mn) / b), None
    best: tuple[float, float, float] | None = None
    for s in [major] if major else _nice_steps((hi - lo * 1.05) / b):
        mn = math.floor(lo * 1.05 / s + 1e-9) * s if lo < 0 else 0.0
        mx = math.ceil((mn + (hi - mn) / b) / s - 1e-9) * s
        if mx <= mn or ((mx - mn) / s > 10 + 1e-9 and not major):
            continue
        if best is None or (mx - mn) < best[1] - best[0] - 1e-12:
            best = (mn, mx, s)
    if best is None:
        mn = lo * 1.05 if lo < 0 else 0.0
        return _num(mn), _num(mn + (hi - mn) / b), None
    return _num(best[0]), _num(best[1]), _num(best[2])


def _nice_candidates(vmin: float, vmax: float, major: float | None = None) -> list[tuple[float, float, float]]:
    big = max(abs(vmin), abs(vmax), 1e-12)
    e0 = math.floor(math.log10(big))
    steps = [major] if major else [m * 10.0**e for e in range(e0 - 4, e0 + 2) for m in (1, 2, 2.5, 5)]
    out, seen = [], set()
    for step in steps:
        for n in range(2, 11):
            k_hi = math.floor(vmin / step + 1e-9)
            k_lo = math.ceil(vmax / step - 1e-9) - n
            if k_hi - k_lo > 60:
                continue
            for k in range(k_lo, k_hi + 1):
                mn, mx = float(f"{k * step:.12g}"), float(f"{(k + n) * step:.12g}")
                if (mn, mx) not in seen:
                    seen.add((mn, mx))
                    out.append((mn, mx, step))
    return out


# --- геометрия -----------------------------------------------------------------------------------


def _model(
    members: list[dict[str, Any]], css: dict[str, Any], locale: str, fscale: float
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Модель группы графиков: подписи (дюймы слайда) и сведения о каждом графике."""
    labels: list[dict[str, Any]] = []
    infos = {}
    for m in members:
        ch = Chart(m["key"], css[m["key"]], m["frame"], DEFAULT_SZ)
        lb, info = chart_labels(ch, locale, fscale, 10)
        labels += lb
        infos[m["key"]] = info
    return labels, infos


def _vcoord(sc: dict[str, Any], plot: list[float], v: float) -> float:
    _, py, _, ph = plot
    t = (v - sc["min"]) / ((sc["max"] - sc["min"]) or 1.0)
    if sc["reversed"]:
        t = 1 - t
    return py + ph - t * ph


def _cat_rev(g: Any, axes: dict[str, Any]) -> bool:
    cax = next((a for a in xp(g, "./c:axId/@val") if a in axes and ln(axes[a]) != "valAx"), None)
    return cax is not None and val(axes[cax].find(C + "scaling"), "orientation", "minMax") == "maxMin"


def _bar_rects(cs: Any, info: dict[str, Any]) -> list[Box]:
    """Все столбцы (сегменты стопок) вертикальных групп графика в дюймах слайда."""
    plot = info["plot_inner_slide"]
    px, _, pw, _ = plot
    axes = _axes(cs)
    out = []
    for g in _groups(cs):
        if ln(g) != "barChart" or val(g, "barDir", "col") != "col":
            continue
        aid = _vax(g, axes)
        if aid is None or aid not in info["axes"]:
            continue
        sc = info["axes"][aid]
        stacked = val(g, "grouping", "clustered") in ("stacked", "percentStacked")
        sers = [num_cache(s, "val") for s in g.findall(C + "ser")]
        n = max([len(v) for v in sers] + [0])
        if not n:
            continue
        gap = float(val(g, "gapWidth", "150")) / 100.0
        ov = float(val(g, "overlap", "100" if stacked else "0")) / 100.0
        k = 1 if stacked else len(sers)
        bw = pw / n / (k - (k - 1) * ov + gap)
        total = bw * (k - (k - 1) * ov)
        rev = _cat_rev(g, axes)
        base = min(max(0.0, sc["min"]), sc["max"])
        pos_acc, neg_acc = [0.0] * n, [0.0] * n
        for j, vals in enumerate(sers):
            for i in range(n):
                v = vals[i] if i < len(vals) else None
                if v is None:
                    continue
                f = (i + 0.5) / n
                c = px + (1 - f if rev else f) * pw
                if stacked:
                    if v >= 0:
                        a, b = pos_acc[i], pos_acc[i] + v
                        pos_acc[i] = b
                    else:
                        a, b = neg_acc[i], neg_acc[i] + v
                        neg_acc[i] = b
                    a = a if a != 0 else base
                    x0, x1_ = c - total / 2, c + total / 2
                else:
                    a, b = base, v
                    cc = c - total / 2 + j * bw * (1 - ov) + bw / 2
                    x0, x1_ = cc - bw / 2, cc + bw / 2
                ya, yb = _vcoord(sc, plot, a), _vcoord(sc, plot, b)
                out.append((x0, min(ya, yb), x1_, max(ya, yb)))
    return out


def _line_points(
    m: dict[str, Any], cs: Any, info: dict[str, Any], labels: list[dict[str, Any]], aid: str
) -> list[dict[str, Any]]:
    """Точки всех серий линий на оси ``aid``: x, значение, полувысота маркера, размер подписи."""
    px, _, pw, _ = info["plot_inner_slide"]
    axes = _axes(cs)
    between = info["axes"][aid]["crossBetween"] != "midCat"
    lbl = {
        (lb["series"], lb["idx"]): lb
        for lb in labels
        if lb["chart"] == m["key"] and lb["kind"] == "line" and lb["axis"] == aid
    }
    pts = []
    for g in _groups(cs):
        if ln(g) != "lineChart" or _vax(g, axes) != aid:
            continue
        rev = _cat_rev(g, axes)
        for s in g.findall(C + "ser"):
            vals = num_cache(s, "val")
            n = len(vals)
            msym = x1(s, "./c:marker/c:symbol/@val")
            mh = 0.0 if msym == "none" else float(x1(s, "./c:marker/c:size/@val") or 5) / 2 / 72.0
            name = _sname(s)
            for i, v in enumerate(vals):
                if v is None:
                    continue
                f = ((i + 0.5) / n) if between else (i / max(1, n - 1))
                x = px + (1 - f if rev else f) * pw
                lb = lbl.get((name, i))
                w = h = 0.0
                if lb is not None:
                    w, h = lb["box"][2] - lb["box"][0], lb["box"][3] - lb["box"][1]
                pts.append(
                    {"series": name, "idx": i, "x": x, "v": v, "mh": mh, "w": w, "h": h, "labelled": lb is not None}
                )
    return pts


# --- положение линии -------------------------------------------------------------------------------


def _constraints(
    pts: list[dict[str, Any]], plot: list[float], obstacles: list[Box], clr: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Нижние (v, f_min) и верхние (v, f_max) ограничения на долю высоты f = a·v + c линии."""
    _, py, _, ph = plot
    lows, highs = [], []
    live = [o for o in obstacles if o[3] > py and o[1] < py + ph]
    for p in pts:
        room = p["mh"] + (LBL_GAP + p["h"] + TOP_PAD if p["labelled"] else 0.0)
        highs.append((p["v"], 1.0 - room / ph))
        half = max(p["w"] / 2, p["mh"]) + 0.02
        lo = p["mh"] / ph
        for o in live:
            if o[2] <= p["x"] - half or o[0] >= p["x"] + half:
                continue
            lo = max(lo, 1.0 - (o[1] - clr - p["mh"] - py) / ph)
        lows.append((p["v"], lo))
    by_ser: dict[str, list[dict[str, Any]]] = {}
    for p in pts:
        by_ser.setdefault(p["series"], []).append(p)
    for ps in by_ser.values():
        ps.sort(key=lambda q: q["x"])
        for p, q in itertools.pairwise(ps):
            xa, xb = p["x"], q["x"]
            if xb - xa <= 1e-9:
                continue
            for o in live:
                x0, x1_ = max(o[0], xa), min(o[2], xb)
                if x1_ <= x0:
                    continue
                lo = 1.0 - (o[1] - SEG_CLR - py) / ph
                for xx in (x0, x1_):
                    t = (xx - xa) / (xb - xa)
                    lows.append((p["v"] + t * (q["v"] - p["v"]), lo))
    return lows, highs


def _interval(a: float, lows: list[tuple[float, float]], highs: list[tuple[float, float]]) -> tuple[float, float]:
    return max(lo - a * v for v, lo in lows), min(hi - a * v for v, hi in highs)


def _solve(
    pts: list[dict[str, Any]],
    lows: list[tuple[float, float]],
    highs: list[tuple[float, float]],
    ph: float,
    s0: float,
    d_floor: float = 0.0,
    slack_req: float = 0.0,
) -> dict[str, Any]:
    """Лучшие (a, c) для f = a·v + c среди размахов d ≥ d_floor с запасом не меньше slack_req дюйма."""
    vs = [p["v"] for p in pts]
    lmin, lmax = min(vs), max(vs)
    span = lmax - lmin
    if span <= 0:
        a = 1.0 / (2.0 * max(abs(lmin), 1e-9))
        lc, uc = _interval(a, lows, highs)
        ok = lc <= uc + 1e-12 and (uc - lc) / 2 * ph >= slack_req - 1e-9
        return {
            "a": a,
            "c": (lc + uc) / 2 if ok else uc,
            "d": 0.0,
            "dmax": 0.0 if ok else -1.0,
            "slack_in": (uc - lc) / 2 * ph,
            "feasible": ok,
            "viol": max(0.0, lc - uc),
        }
    ds = sorted({0.004 * (1.045**k) for k in range(125)} | ({s0} if s0 and s0 < 1 else set()))
    ds = [d for d in ds if d < 1.0]
    best: tuple[float, float, float, float, float] | None = None
    least: tuple[float, float, float, float] | None = None
    dmax = -1.0
    for d in ds:
        a = d / span
        lc, uc = _interval(a, lows, highs)
        viol = lc - uc
        if least is None or viol < least[0]:
            least = (viol, a, uc, d)
        if viol > 1e-12:
            continue
        dmax = max(dmax, d)
        slack = (uc - lc) / 2 * ph
        if d < d_floor - 1e-12 or slack < slack_req - 1e-9:
            continue
        score = (PEN_SHAPE * abs(math.log(d / s0)) if s0 else 0.0) - BONUS_SLACK * min(1.0, slack / 0.10)
        if best is None or score < best[0] - 1e-12:
            best = (score, a, (lc + uc) / 2, d, slack)
    assert least is not None
    if best is None and dmax > 0:
        return {
            "a": least[1],
            "c": least[2],
            "d": least[3],
            "dmax": dmax,
            "slack_in": 0.0,
            "feasible": False,
            "viol": 0.0,
        }
    if best is None:
        viol, a, uc, d = least
        return {"a": a, "c": uc, "d": d, "dmax": -1.0, "slack_in": -viol / 2 * ph, "feasible": False, "viol": viol}
    return {
        "a": best[1],
        "c": best[2],
        "d": best[3],
        "dmax": dmax,
        "slack_in": best[4],
        "feasible": True,
        "viol": 0.0,
        "score": best[0],
    }


def _check(a: float, c: float, lows: list[tuple[float, float]], highs: list[tuple[float, float]]) -> float:
    return min([a * v + c - lo for v, lo in lows] + [hi - a * v - c for v, hi in highs])


# --- группы наложенных графиков ----------------------------------------------------------------


def _members(slide: Any) -> list[dict[str, Any]]:
    out = []
    for k, (sh, frame) in enumerate(chart_frames(slide.shapes)):
        out.append(
            {
                "key": f"{k}:{sh.name}",
                "name": sh.name,
                "id": sh.shape_id,
                "frame": tuple(frame),
                "live": sh.chart._chartSpace,
            }
        )
    return out


def _clusters(members: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = members[i]["frame"], members[j]["frame"]
            if min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0]) and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1]):
                parent[find(i)] = find(j)
    out: dict[int, list[dict[str, Any]]] = {}
    for i, m in enumerate(members):
        out.setdefault(find(i), []).append(m)
    return list(out.values())


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def fix_slide_labels(
    slide: Any,
    only: set[int] | None = None,
    clearance: float = CLEARANCE,
    locale: str = "ru",
    font_scale: float = 1.0,
    pair_share: bool = True,
) -> list[dict[str, Any]]:
    """Развести подписи на графиках слайда. ``only`` — id фигур: правятся только группы
    наложенных графиков, где есть хотя бы один из них. Возвращает журнал решений."""
    log: list[dict[str, Any]] = []
    for cl in _clusters(_members(slide)):
        if only is not None and not any(m["id"] in only for m in cl):
            continue
        log += _fix_cluster(cl, max(CLEARANCE, clearance), locale, font_scale, pair_share)
    return log


def _fix_cluster(
    cl: list[dict[str, Any]], clearance: float, locale: str, fscale: float, pair_share: bool
) -> list[dict[str, Any]]:
    names = [m["name"] for m in cl]

    def rec(**kw: Any) -> dict[str, Any]:
        return {"cluster": names, **kw}

    base = {m["key"]: _copy(m["live"]) for m in cl}
    by_key = {m["key"]: m for m in cl}

    labels0, infos0 = _model(cl, base, locale, fscale)
    lbl_line = {(lb["chart"], lb["axis"]) for lb in labels0 if lb["kind"] == "line"}
    lbl_bar = {lb["chart"] for lb in labels0 if lb["kind"] == "bar"}
    if not lbl_line:
        return [rec(action="skip", reason="нет линии с подписями данных")]
    line_axes: list[tuple[str, str]] = []
    for key, aid in sorted(lbl_line):
        cs = base[key]
        axes = _axes(cs)
        gs = _groups(cs)
        bar_axes_ids = {_vax(g, axes) for g in gs if ln(g) == "barChart"}
        lgs = [g for g in gs if ln(g) == "lineChart" and _vax(g, axes) == aid]
        why = None
        if aid in bar_axes_ids:
            why = "линия на одной оси со столбцами"
        elif any(val(g, "grouping", "standard") in ("stacked", "percentStacked") for g in lgs):
            why = "линия с накоплением"
        elif val(axes[aid].find(C + "scaling"), "orientation", "minMax") == "maxMin":
            why = "перевёрнутая ось линии"
        elif axes[aid].find(C + "scaling").find(C + "logBase") is not None:
            why = "логарифмическая ось линии"
        if why:
            return [rec(chart=by_key[key]["name"], action="skip", reason=why)]
        line_axes.append((key, aid))
    line_keys = {k for k, _ in line_axes}

    parts: list[Any] = []
    others: list[Any] = []
    for m in cl:
        if m["key"] not in lbl_bar:
            continue
        p = infos0[m["key"]]["plot_inner_slide"]
        share = max(
            _overlap(p[1], p[1] + p[3], q[1], q[1] + q[3]) / max(1e-6, min(p[3], q[3]))
            for q in (infos0[k]["plot_inner_slide"] for k in line_keys)
        )
        (parts if (m["key"] in line_keys or share >= 0.5) else others).append(m)
    if not parts:
        return [rec(action="skip", reason="нет столбцов с подписями в области построения линии")]
    for m in parts + [by_key[k] for k in line_keys]:
        cs = base[m["key"]]
        axes = _axes(cs)
        for g in _groups(cs):
            kind = ln(g)
            why = None
            if kind not in ("barChart", "lineChart"):
                why = f"группа {kind} в графике"
            elif kind == "barChart" and val(g, "barDir", "col") == "bar":
                why = "горизонтальные столбцы"
            elif kind == "barChart" and val(g, "grouping", "clustered") == "percentStacked":
                why = "нормированные (100 %) столбцы"
            elif kind == "barChart":
                aid2 = _vax(g, axes)
                if aid2 is None:
                    why = "столбцы без оси значений"
                elif axes[aid2].find(C + "scaling").find(C + "logBase") is not None:
                    why = "логарифмическая ось столбцов"
                elif val(axes[aid2].find(C + "scaling"), "orientation", "minMax") == "maxMin":
                    why = "перевёрнутая ось столбцов"
            if why:
                return [rec(chart=m["name"], action="skip", reason=why)]

    # Ничего не делать, если при текущих осях линия не подходит к подписям столбцов.
    obst0 = [tuple(lb["box"]) for lb in labels0 if lb["kind"] == "bar"]
    conflicts = []
    for lb in labels0:
        if lb["kind"] != "line":
            continue
        b = lb["box"]
        grown = (b[0] - NOOP_GAP, b[1] - NOOP_GAP, b[2] + NOOP_GAP, b[3] + NOOP_GAP)
        if any(inter(grown, o) > 0 for o in obst0):
            conflicts.append(f"подпись {lb['series']}[{lb['idx']}]")
    for key, aid in line_axes:
        for p in _line_points(by_key[key], base[key], infos0[key], labels0, aid):
            y = _vcoord(infos0[key]["axes"][aid], infos0[key]["plot_inner_slide"], p["v"])
            mb = (
                p["x"] - p["mh"] - NOOP_GAP,
                y - p["mh"] - NOOP_GAP,
                p["x"] + p["mh"] + NOOP_GAP,
                y + p["mh"] + NOOP_GAP,
            )
            if any(inter(mb, o) > 0 for o in obst0):
                conflicts.append(f"маркер {p['series']}[{p['idx']}]")
    before = _count(labels0)
    if not conflicts:
        return [rec(action="kept", reason="подписи линии не задевают подписи столбцов", pairs=before)]

    def ncat(cs: Any) -> int:
        return max([len(num_cache(s, "val")) for s in cs.iter(C + "ser")] + [0])

    bar_axes: list[dict[str, Any]] = []
    for role, ms in (("bars", parts), ("pair", others)):
        for m in ms:
            cs, info = base[m["key"]], infos0[m["key"]]
            axes = _axes(cs)
            if role == "pair":
                if not pair_share:
                    continue
                pp = info["plot_inner_slide"]
                if any(
                    ln(g) != "barChart"
                    or val(g, "barDir", "col") != "col"
                    or val(g, "grouping", "clustered") == "percentStacked"
                    or _vax(g, axes) is None
                    or axes[str(_vax(g, axes))].find(C + "scaling").find(C + "logBase") is not None
                    for g in _groups(cs)
                ):
                    continue
                twin = [
                    q
                    for q in parts
                    if ncat(base[q["key"]]) == ncat(cs)
                    and _overlap(
                        pp[0],
                        pp[0] + pp[2],
                        infos0[q["key"]]["plot_inner_slide"][0],
                        infos0[q["key"]]["plot_inner_slide"][0] + infos0[q["key"]]["plot_inner_slide"][2],
                    )
                    >= 0.5 * min(pp[2], infos0[q["key"]]["plot_inner_slide"][2])
                ]
                if not twin:
                    continue
            for aid in sorted({str(_vax(g, axes)) for g in _groups(cs) if ln(g) == "barChart"}):
                sc = info["axes"][aid]
                lo, hi = min(0.0, sc["data_min"] or 0.0), max(0.0, sc["data_max"] or 0.0)
                rng = (sc["max"] - sc["min"]) or 1.0
                ax = axes[aid]
                bar_axes.append(
                    {
                        "key": m["key"],
                        "aid": aid,
                        "lo": lo,
                        "hi": hi,
                        "cur": (sc["min"], sc["max"]),
                        "b_cur": (hi - sc["min"]) / rng if hi > lo else 1.0,
                        "visible": not _axis_hidden(ax),
                        "role": role,
                        "major": float(val(ax, "majorUnit")) if val(ax, "majorUnit") else None,
                    }
                )
    b_start = min(B_START_MAX, max(a["b_cur"] for a in bar_axes if a["role"] == "bars"))

    lines = []
    for key, aid in line_axes:
        sc = infos0[key]["axes"][aid]
        vs = [p["v"] for p in _line_points(by_key[key], base[key], infos0[key], labels0, aid)]
        ax = _axes(base[key])[aid]
        lines.append(
            {
                "key": key,
                "aid": aid,
                "s0": (max(vs) - min(vs)) / ((sc["max"] - sc["min"]) or 1.0),
                "visible": not _axis_hidden(ax),
                "major": float(val(ax, "majorUnit")) if val(ax, "majorUnit") else None,
                "clr": clearance + (EST_EXTRA if infos0[key]["plot_from"] == "estimate" else 0.0),
                "cur": (sc["min"], sc["max"]),
                "vmin": min(vs),
                "vmax": max(vs),
            }
        )

    cache: dict[Any, dict[str, Any]] = {}

    def geo(scales: dict[tuple[str, str], tuple[float, float]]) -> dict[str, Any]:
        sig = tuple(sorted(scales.items()))
        if sig in cache:
            return cache[sig]
        css = {k: _copy(v) for k, v in base.items()}
        for (key, aid), (mn, mx) in scales.items():
            if (mn, mx) != next(a["cur"] for a in bar_axes if (a["key"], a["aid"]) == (key, aid)):
                _set_scaling(_axes(css[key])[aid], mn, mx)
        labels, infos = _model(cl, css, locale, fscale)
        obst: list[Box] = [tuple(lb["box"]) for lb in labels if lb["kind"] == "bar"]
        for m in cl:
            obst += _bar_rects(css[m["key"]], infos[m["key"]])
        sols = []
        for ln_ in lines:
            info = infos[ln_["key"]]
            plot = info["plot_inner_slide"]
            pts = _line_points(by_key[ln_["key"]], css[ln_["key"]], info, labels, ln_["aid"])
            lows, highs = _constraints(pts, plot, obst, ln_["clr"])
            sols.append({"pts": pts, "lows": lows, "highs": highs, "plot": plot})
        res = {"scales": scales, "sols": sols}
        cache[sig] = res
        return res

    def evaluate(b: float) -> dict[str, Any]:
        scales: dict[tuple[str, str], tuple[float, float]] = {}
        steps: dict[tuple[str, str], float | None] = {}
        eff = []
        for a in bar_axes:
            if a["role"] != "bars":
                continue
            mn, mx, st = _bar_scale(a["lo"], a["hi"], min(b, a["b_cur"]), a["visible"], a["major"])
            if _close(mn, a["cur"][0]) and _close(mx, a["cur"][1]):
                mn, mx = a["cur"]
            scales[(a["key"], a["aid"])] = (mn, mx)
            steps[(a["key"], a["aid"])] = st
            if a["hi"] > a["lo"]:
                eff.append((a["hi"] - mn) / ((mx - mn) or 1.0))
        b_eff = min(eff) if eff else b
        for a in bar_axes:
            if a["role"] != "pair":
                continue
            mn, mx, st = _bar_scale(a["lo"], a["hi"], min(b_eff, a["b_cur"]), a["visible"], a["major"])
            if _close(mn, a["cur"][0]) and _close(mx, a["cur"][1]):
                mn, mx = a["cur"]
            scales[(a["key"], a["aid"])] = (mn, mx)
            steps[(a["key"], a["aid"])] = st
        return {**geo(scales), "b": b, "b_eff": b_eff, "steps": steps}

    def solve(tier: str, ln_: dict[str, Any], g: dict[str, Any]) -> dict[str, Any]:
        ph = g["plot"][3]
        want = min(ln_["s0"], WANT_CAP) if ln_["vmax"] > ln_["vmin"] else 0.0
        floor = {"want": want, "min": min(want, MIN_BAND / ph), "fit": 0.0, "none": 0.0}[tier]
        sol = _solve(g["pts"], g["lows"], g["highs"], ph, ln_["s0"], floor, SLACK_WANT if tier == "want" else 0.0)
        sol.update(g)
        return sol

    grid = []
    b = b_start
    while b >= B_FLOOR - 1e-9:
        grid.append(round(b, 4))
        b -= B_STEP
    chosen: tuple[dict[str, Any], str] | None = None
    for tier in ("want", "min", "fit"):
        for b in grid:
            if tier != "fit" and b < B_MIN - 1e-9:
                break
            r = evaluate(b)
            sols = [solve(tier, ln_, g) for ln_, g in zip(lines, r["sols"], strict=True)]
            if all(s["feasible"] for s in sols):
                chosen = ({**r, "sols": sols}, tier)
                break
        if chosen:
            break
    log = []
    if chosen is None:
        cands = []
        for b in grid:
            r = evaluate(b)
            cands.append({**r, "sols": [solve("none", ln_, g) for ln_, g in zip(lines, r["sols"], strict=True)]})
        chosen = (min(cands, key=lambda r: sum(s["viol"] for s in r["sols"])), "none")
        log.append(
            rec(
                action="warn",
                reason=f"область построения слишком низкая: линия не уходит от столбцов ни при какой доле "
                f"столбцов от {B_FLOOR:.2f}; линия поднята настолько, насколько позволяют её подписи",
            )
        )
    r, tier = chosen

    # Пара: та же доля, что у столбцов графика с линией, но без новых наложений подписей её столбцов.
    scales = dict(r["scales"])
    steps = dict(r["steps"])
    for a in bar_axes:
        if a["role"] != "pair":
            continue
        k = (a["key"], a["aid"])
        m = by_key[a["key"]]

        def bar_pairs(mn: float, mx: float, a: dict[str, Any] = a, m: dict[str, Any] = m) -> int:
            cs = _copy(base[a["key"]])
            if (mn, mx) != a["cur"]:
                _set_scaling(_axes(cs)[a["aid"]], mn, mx)
            lb, _ = _model([m], {a["key"]: cs}, locale, fscale)
            return sum(p["severity"] == "overlap" for p in find_pairs(lb, ("bar-bar",), margin=PAIR_MARGIN))

        n0 = bar_pairs(*a["cur"])
        bb = min(r["b_eff"], a["b_cur"])
        while True:
            mn, mx, st = _bar_scale(a["lo"], a["hi"], bb, a["visible"], a["major"])
            if (_close(mn, a["cur"][0]) and _close(mx, a["cur"][1])) or bb >= a["b_cur"] - 1e-9:
                mn, mx, st = a["cur"][0], a["cur"][1], None
                break
            if bar_pairs(mn, mx) <= n0:
                break
            bb = min(a["b_cur"], bb + B_STEP)
        scales[k], steps[k] = (mn, mx), st
    if scales != r["scales"]:
        g2 = geo(scales)
        if any(
            _check(s["a"], s["c"], g["lows"], g["highs"]) < -1e-9 for s, g in zip(r["sols"], g2["sols"], strict=True)
        ):
            scales, steps = dict(r["scales"]), dict(r["steps"])
    r = {**r, "scales": scales, "steps": steps}

    # Границы оси линии: точные у скрытой, круглые у видимой.
    line_scales = []
    for ln_, s in zip(lines, r["sols"], strict=True):
        a_, c_ = s["a"], s["c"]
        mn, mx = -c_ / a_, (1 - c_) / a_
        lstep = None
        if ln_["visible"]:
            best = None
            for cmn, cmx, cst in _nice_candidates(ln_["vmin"], ln_["vmax"], ln_["major"]):
                if cmn > ln_["vmin"] + 1e-12 or cmx < ln_["vmax"] - 1e-12 or (ln_["vmin"] >= 0 and cmn < 0):
                    continue
                aa = 1.0 / (cmx - cmn)
                slack = _check(aa, -cmn * aa, s["lows"], s["highs"])
                if slack < -1e-9:
                    continue
                d = (ln_["vmax"] - ln_["vmin"]) * aa
                score = (PEN_SHAPE * abs(math.log(d / ln_["s0"])) if ln_["s0"] and d > 0 else 0.0) - BONUS_SLACK * min(
                    1.0, slack * s["plot"][3] / 0.10
                )
                if best is None or score < best[0]:
                    best = (score, cmn, cmx, cst)
            if best:
                mn, mx, lstep = best[1], best[2], best[3]
        line_scales.append((_num(mn), _num(mx), lstep))

    log.append(rec(action="plan", tier=tier, bar_share=r["b"], bar_share_effective=round(r["b_eff"], 3)))

    # Запись в живой XML графиков.
    for a in bar_axes:
        mn, mx = r["scales"][(a["key"], a["aid"])]
        if (mn, mx) == a["cur"]:
            continue
        m = by_key[a["key"]]
        ax = _axes(m["live"])[a["aid"]]
        _set_scaling(ax, mn, mx)
        st = r["steps"].get((a["key"], a["aid"]))
        if a["visible"] and st and a["major"] is None:
            _set_major(ax, st)
        log.append({"chart": m["name"], "action": "set_axis_scaling", "role": a["role"], "min": mn, "max": mx})
    for ln_, (mn, mx, lstep) in zip(lines, line_scales, strict=True):
        m = by_key[ln_["key"]]
        axes = _axes(m["live"])
        ax = axes[ln_["aid"]]
        _set_scaling(ax, mn, mx)
        if lstep and ln_["major"] is None:
            _set_major(ax, _num(lstep))
        if not ln_["visible"]:
            _hidden_axis_numfmt(m["live"], axes, ln_["aid"], ax)
        log.append({"chart": m["name"], "action": "set_axis_scaling", "role": "line", "min": mn, "max": mx})
    for key, aid in line_axes:  # подписи линии — над точками
        m = by_key[key]
        axes = _axes(m["live"])
        for g in _groups(m["live"]):
            if ln(g) != "lineChart" or _vax(g, axes) != aid:
                continue
            gd = g.find(C + "dLbls")
            for s in g.findall(C + "ser"):
                sd = s.find(C + "dLbls")
                target = sd if sd is not None else gd
                if target is None or _flag(target, "delete"):
                    continue
                if val(target, "dLblPos") != "t":
                    _set_dlblpos(target, "t")
    for m in parts:  # ручные сдвиги подписей точек подогнаны под демо-данные шаблона
        for s in m["live"].iter(C + "ser"):
            sd = s.find(C + "dLbls")
            if sd is not None:
                _drop_point_layouts(s, sd)
    for key, aid in line_axes:
        m = by_key[key]
        axes = _axes(m["live"])
        for g in _groups(m["live"]):
            if ln(g) != "lineChart" or _vax(g, axes) != aid:
                continue
            for s in g.findall(C + "ser"):
                for d in xp(s, "./c:dLbls/c:dLbl"):
                    if not _flag(d, "delete") and val(d, "dLblPos") != "t":
                        _set_dlblpos(d, "t")

    labels1, _ = _model(cl, {m["key"]: _copy(m["live"]) for m in cl}, locale, fscale)
    log.append(rec(action="verify", pairs_before=before, pairs_after=_count(labels1)))
    return log


def label_overlaps(slide: Any, locale: str = "ru") -> list[dict[str, Any]]:
    """Наложения подписей линии на подписи столбцов и друг на друга в группах графиков слайда,
    где есть и линия, и столбцы с подписями, — как их видит модель подписей. Для проверки
    готового отчёта: слайд не меняется."""
    out: list[dict[str, Any]] = []
    for cl in _clusters(_members(slide)):
        labels, _ = _model(cl, {m["key"]: m["live"] for m in cl}, locale, 1.0)
        if {"line", "bar"} <= {lb["kind"] for lb in labels}:
            out += [p for p in find_pairs(labels, ("line-bar", "line-line")) if p["severity"] == "overlap"]
    return out


def _count(labels: list[dict[str, Any]]) -> dict[str, int]:
    pairs = find_pairs(labels, ("line-bar", "line-line"))
    overlap = sum(p["severity"] == "overlap" for p in pairs)
    return {"overlap": overlap, "near": len(pairs) - overlap}
