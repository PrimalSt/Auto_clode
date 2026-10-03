"""Модель подписей данных графиков PowerPoint: где окажутся подписи на слайде и какие из них
наложатся друг на друга. Используется правилом подписей комбинированных графиков
(``label_bands``); перенесено из прототипа, проверенного на шаблоне пользователя.

Всё в дюймах слайда (начало — левый верхний угол). Несколько графиков слайда (например, два
наложенных) живут в одной системе координат, поэтому находятся и наложения между графиками.

Модель повторяет поведение Office, а не рисует график:

- рамка графика — ``xfrm`` фигуры (с учётом групп);
- область построения — ``c:plotArea/c:layout/c:manualLayout``, а без него — оценка: отступы,
  заголовок, легенда, подписи осей (даже белые: место они всё равно занимают);
- оси — ``c:scaling`` min/max, если заданы, иначе автомасштаб Office: минимум 0, если данные
  не отрицательные и разброс не меньше 1/6 максимума; максимум — данные плюс 5 % с округлением
  до «круглого» шага (1/2/5 × 10^k, не больше 10 делений);
- подписи столбцов ``ctr``/``inEnd``/``inBase``/``outEnd`` (по умолчанию: группировка — снаружи,
  накопление — в центре), ширина столбца — из ``gapWidth``/``overlap``; подписи линии
  ``t``/``b``/``l``/``r``/``ctr`` (по умолчанию справа) рядом с маркером; ручные сдвиги подписей
  точек (``c:dLbl/c:layout``) применяются;
- текст подписи — по формату числа (подпись точки > серии > группы > формат данных), ширина —
  по метрикам Arial (``textwidth``).
"""

from __future__ import annotations

import contextlib
import math
import re
from typing import Any

from lxml import etree

from .textwidth import text_width_in

NS = {
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
C = f"{{{NS['c']}}}"
A = f"{{{NS['a']}}}"
EMU = 914400.0

# Размеры в дюймах — как у Office, подобраны по отрисовке проверочного отчёта.
CHART_PAD = 0.10  # внутренний отступ области диаграммы
AXIS_GAP = 0.08  # подпись оси — область построения
LEGEND_KEY = 0.35  # значок легенды и отступ
LBL_GAP = 0.03  # подпись — конец столбца или маркер
DEFAULT_INS = (38100 / EMU, 19050 / EMU, 38100 / EMU, 19050 / EMU)  # слева, сверху, справа, снизу
DEFAULT_SZ = 10.0  # пт, размер текста графика в Office по умолчанию


def xp(el: Any, path: str) -> list[Any]:
    return etree._Element.xpath(el, path, namespaces=NS)


def x1(el: Any, path: str) -> Any:
    r = xp(el, path)
    return r[0] if r else None


def val(el: Any, tag: str, default: Any = None) -> Any:
    if el is None:
        return default
    e = el.find(C + tag)
    return default if e is None or e.get("val") is None else e.get("val")


def ln(el: Any) -> str:
    return str(etree.QName(el).localname)


# --- формат числа -----------------------------------------------------------------------------


def _split_sections(code: str) -> list[str]:
    out, cur, q = [], "", False
    for ch in code:
        if ch == '"':
            q = not q
        if ch == ";" and not q:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def format_value(v: float | None, code: str | None, locale: str = "ru") -> str:
    """Формат Excel → текст: секции для положительных и отрицательных, %, знаки после
    запятой, разделитель разрядов, литералы, деление на тысячи (``#,##0,``)."""
    if v is None:
        return ""
    code = code or "General"
    secs = _split_sections(code)
    sec = secs[0]
    neg_own = False
    if v < 0 and len(secs) > 1 and secs[1].strip():
        sec, neg_own = secs[1], True
    elif v == 0 and len(secs) > 2 and secs[2].strip():
        sec = secs[2]
    sec = re.sub(r"\[[^\]]*\]", "", sec)  # [Red], [$-419] …
    if sec.strip().lower() in ("general", "основной", ""):
        s = f"{v:.10g}"
        if "e" not in s and "." in s:
            s = s.rstrip("0").rstrip(".")
        return s.replace(".", ",") if locale == "ru" else s
    lits: list[str] = []
    tpl, i = "", 0
    while i < len(sec):
        ch = sec[i]
        if ch == '"':
            j = sec.find('"', i + 1)
            j = len(sec) if j < 0 else j
            lits.append(sec[i + 1 : j])
            tpl += f"\x00{len(lits) - 1}\x00"
            i = j + 1
            continue
        if ch == "\\" and i + 1 < len(sec):
            lits.append(sec[i + 1])
            tpl += f"\x00{len(lits) - 1}\x00"
            i += 2
            continue
        if ch in "_*" and i + 1 < len(sec):
            lits.append(" " if ch == "_" else "")
            tpl += f"\x00{len(lits) - 1}\x00"
            i += 2
            continue
        tpl += ch
        i += 1
    x = abs(v) if v < 0 else v
    if "%" in tpl:
        x *= 100.0 * (100.0 ** (tpl.count("%") - 1))
    m = re.search(r"[#0?][#0?,]*(\.[#0?]*)?|\.[#0?]+", tpl)
    if not m:
        num = ""
        body = tpl
    else:
        pat = m.group(0)
        int_part, _, dec_part = pat.partition(".")
        trailing = len(int_part) - len(int_part.rstrip(","))
        int_core = int_part.rstrip(",")
        x /= 1000.0**trailing
        dec = len(dec_part)
        grouping = "," in int_core
        s = f"{x:.{dec}f}"
        ip, _, dp = s.partition(".")
        if ip == "0" and int_core.count("0") == 0:
            ip = ""
        if grouping and ip:
            ip = f"{int(ip):,}"
        gsep, dsep = (" ", ",") if locale == "ru" else (",", ".")
        num = ip.replace(",", gsep) + ((dsep + dp) if dec else "")
        body = tpl[: m.start()] + "\x02" + tpl[m.end() :]
    res = body.replace("\x02", num)
    res = re.sub("\x00(\\d+)\x00", lambda mm: lits[int(mm.group(1))], res)
    if v < 0 and not neg_own and m:
        res = "-" + res
    return res


# --- масштаб осей --------------------------------------------------------------------------


def nice_step(span: float, max_steps: int = 10) -> float:
    if span <= 0:
        return 1.0
    k = math.floor(math.log10(span / max_steps))
    for e in (k - 1, k, k + 1, k + 2):
        for mult in (1, 2, 5):
            s = mult * 10.0**e
            if span / s <= max_steps + 1e-9:
                return s
    return float(10.0 ** (k + 2))


def auto_scale(
    dmin: float | None, dmax: float | None, max_steps: int = 10, percent_stacked: bool = False
) -> tuple[float, float, float]:
    """Автоматические границы оси значений, как у Office: (минимум, максимум, шаг)."""
    if percent_stacked:
        return 0.0, 1.0, 0.1
    if dmin is None or dmax is None:
        return 0.0, 1.0, 0.2
    if dmin == dmax:
        dmin, dmax = min(0.0, dmin), max(0.0, dmax)
        if dmin == dmax:
            return 0.0, 1.0, 0.2
    lo = 0.0 if dmin >= 0 else dmin
    hi = 0.0 if dmax <= 0 else dmax
    if dmin > 0 and (dmax - dmin) < dmax / 6.0:
        lo = dmin - (dmax - dmin) / 2.0
    if dmax < 0 and (dmax - dmin) < -dmin / 6.0:
        hi = dmax + (dmax - dmin) / 2.0
    span = hi - lo
    hi_p = hi + 0.05 * span if hi > 0 else hi
    lo_p = lo - 0.05 * span if lo < 0 else lo
    step = nice_step(hi_p - lo_p, max_steps)
    while True:
        mx = math.ceil(hi_p / step - 1e-9) * step
        mn = math.floor(lo_p / step + 1e-9) * step
        if (mx - mn) / step <= max_steps + 1e-9:
            return mn, mx, step
        step = nice_step((mx - mn) * 1.0001, max_steps)


# --- модель графика --------------------------------------------------------------------------


def num_cache(ser: Any, tag: str) -> list[float | None]:
    pts = xp(ser, f"./c:{tag}//c:numCache/c:pt") or xp(ser, f"./c:{tag}//c:numLit/c:pt")
    cnt = x1(ser, f"./c:{tag}//c:ptCount/@val")
    n = int(cnt) if cnt is not None else len(pts)
    out: list[float | None] = [None] * n
    for p in pts:
        i = int(p.get("idx"))
        if i < n:
            with contextlib.suppress(TypeError, ValueError):
                out[i] = float(p.findtext(C + "v"))
    return out


def _str_cache(ser: Any) -> list[str]:
    pts = xp(ser, "./c:cat//c:pt")
    cnt = x1(ser, "./c:cat//c:ptCount/@val")
    n = int(cnt) if cnt is not None else len(pts)
    out = [""] * n
    for p in pts:
        i = int(p.get("idx"))
        if i < n:
            out[i] = p.findtext(C + "v") or ""
    return out


def _txpr(el: Any) -> tuple[float | None, bool | None]:
    """Размер (пт) и полужирность из ``c:txPr`` элемента."""
    if el is None:
        return None, None
    r = xp(el, "./c:txPr/a:p/a:pPr/a:defRPr")
    if not r:
        return None, None
    sz = r[0].get("sz")
    b = r[0].get("b")
    return (int(sz) / 100.0 if sz else None), (b in ("1", "true") if b is not None else None)


def _insets(el: Any) -> tuple[float, float, float, float] | None:
    bp = x1(el, "./c:txPr/a:bodyPr") if el is not None else None
    if bp is None:
        return None
    vals = [
        int(bp.get(k)) / EMU if bp.get(k) is not None else dv
        for k, dv in zip(("lIns", "tIns", "rIns", "bIns"), DEFAULT_INS, strict=True)
    ]
    return vals[0], vals[1], vals[2], vals[3]


def _manual(el: Any) -> dict[str, str] | None:
    m = x1(el, "./c:layout/c:manualLayout") if el is not None else None
    if m is None:
        return None
    return {ln(ch): ch.get("val") for ch in m}


class Chart:
    def __init__(self, name: str, cs: Any, frame: tuple[float, float, float, float], default_sz: float):
        self.name, self.cs = name, cs
        self.fx, self.fy, self.W, self.H = frame
        sz, b = _txpr(cs)
        self.sz = sz or default_sz
        self.bold = bool(b)
        self.pa = x1(cs, "./c:chart/c:plotArea")
        self.groups = [g for g in self.pa if ln(g).endswith("Chart")]
        self.axes = {val(a, "axId"): a for a in self.pa if ln(a).endswith("Ax")}


def chart_frames(shapes: Any, ox: float = 0.0, oy: float = 0.0, sx: float = 1.0, sy: float = 1.0) -> Any:
    """Графики слайда с рамками в дюймах слайда (с учётом групп): (фигура, (x, y, ширина, высота))."""
    for sh in shapes:
        if sh.shape_type is not None and sh.shape_type == 6:  # группа
            xf = sh._element.grpSpPr.find(A + "xfrm")
            off, ext = xf.find(A + "off"), xf.find(A + "ext")
            choff, chext = xf.find(A + "chOff"), xf.find(A + "chExt")
            gsx = sx * (int(ext.get("cx")) / max(1, int(chext.get("cx"))))
            gsy = sy * (int(ext.get("cy")) / max(1, int(chext.get("cy"))))
            gox = ox + sx * int(off.get("x")) - gsx * int(choff.get("x"))
            goy = oy + sy * int(off.get("y")) - gsy * int(choff.get("y"))
            yield from chart_frames(sh.shapes, gox, goy, gsx, gsy)
        elif getattr(sh, "has_chart", False) and sh.has_chart:
            yield (
                sh,
                ((ox + sx * sh.left) / EMU, (oy + sy * sh.top) / EMU, sx * sh.width / EMU, sy * sh.height / EMU),
            )


def _series_of(ch: Chart) -> list[tuple[Any, list[dict[str, Any]]]]:
    out = []
    for g in ch.groups:
        sers = []
        for s in g.findall(C + "ser"):
            name = "".join(xp(s, "./c:tx//c:v/text()")) or f"series {val(s, 'idx')}"
            sers.append(
                {
                    "el": s,
                    "name": name,
                    "vals": num_cache(s, "val"),
                    "fmt": x1(s, "./c:val//c:formatCode/text()") or "General",
                    "cats": _str_cache(s),
                }
            )
        out.append((g, sers))
    return out


def _group_range(gtype: str, grouping: str, sers: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    vals = [v for s in sers for v in s["vals"] if v is not None]
    if not vals:
        return None, None
    if grouping in ("stacked", "percentStacked") and gtype in ("barChart", "lineChart", "areaChart"):
        n = max(len(s["vals"]) for s in sers)
        pos = [sum(max(0.0, (s["vals"][i] or 0.0)) for s in sers if i < len(s["vals"])) for i in range(n)]
        neg = [sum(min(0.0, (s["vals"][i] or 0.0)) for s in sers if i < len(s["vals"])) for i in range(n)]
        return min([*neg, 0.0]), max([*pos, 0.0])
    return min(vals), max(vals)


def _axis_label_width(
    ax: Any, vmin: float, vmax: float, step: float, sz: float, fscale: float, locale: str, fmt_src: str | None
) -> float:
    code = x1(ax, "./c:numFmt/@formatCode") or "General"
    if x1(ax, "./c:numFmt/@sourceLinked") == "1" and fmt_src:
        code = fmt_src
    w = 0.0
    v = vmin
    for _ in range(40):
        if v > vmax + step * 1e-6:
            break
        w = max(w, text_width_in(format_value(round(v, 12), code, locale), sz, False, fscale))
        v += step
    return w


def chart_labels(
    ch: Chart, locale: str = "ru", fscale: float = 1.0, max_steps: int = 10
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Подписи данных графика (прямоугольники в дюймах слайда) и сведения о графике: рамка,
    область построения, действующие границы осей."""
    W, H = ch.W, ch.H
    groups = _series_of(ch)
    ncat = max([len(s["vals"]) for _, ss in groups for s in ss] + [0])
    cats = next((s["cats"] for _, ss in groups for s in ss if s["cats"]), [""] * ncat)

    ax_groups: dict[str, list[tuple[Any, list[dict[str, Any]]]]] = {}
    for g, sers in groups:
        for aid in xp(g, "./c:axId/@val"):
            ax_groups.setdefault(aid, []).append((g, sers))
    scales: dict[str, dict[str, Any]] = {}
    for aid, ax in ch.axes.items():
        if ln(ax) != "valAx":
            continue
        lo: float | None = None
        hi: float | None = None
        pst, fmt_src = False, None
        for g, sers in ax_groups.get(aid, []):
            gt = ln(g)
            grp = val(g, "grouping", "clustered" if gt == "barChart" else "standard")
            pst = pst or grp == "percentStacked"
            g_lo, g_hi = _group_range(gt, grp, sers)
            if g_lo is not None and g_hi is not None:
                lo = g_lo if lo is None else min(lo, g_lo)
                hi = g_hi if hi is None else max(hi, g_hi)
            if fmt_src is None and sers:
                fmt_src = sers[0]["fmt"]
        mn, mx, step = auto_scale(lo, hi, max_steps, pst)
        sc = ax.find(C + "scaling")
        emn, emx = val(sc, "min"), val(sc, "max")
        if emn is not None:
            mn = float(emn)
        if emx is not None:
            mx = float(emx)
        if val(ax, "majorUnit"):
            step = float(val(ax, "majorUnit"))
        scales[aid] = {
            "min": mn,
            "max": mx,
            "step": step,
            "reversed": val(sc, "orientation", "minMax") == "maxMin",
            "auto_min": emn is None,
            "auto_max": emx is None,
            "data_min": lo,
            "data_max": hi,
            "fmt_src": fmt_src,
            "pos": val(ax, "axPos"),
            "deleted": val(ax, "delete", "0") in ("1", "true"),
            "tick_lbl": val(ax, "tickLblPos", "nextTo"),
            "crossBetween": val(ax, "crossBetween", "between"),
        }

    horiz = any(val(g, "barDir") == "bar" for g, _ in groups)
    plot, how = _plot_area(ch, scales, cats, horiz, locale, fscale)
    px, py, pw, ph = plot

    def vcoord(aid: str, v: float) -> float:
        s = scales[aid]
        t = (v - s["min"]) / ((s["max"] - s["min"]) or 1.0)
        if s["reversed"]:
            t = 1 - t
        return (px + t * pw) if horiz else (py + ph - t * ph)

    def ccoord(i: int, n: int, between: bool = True, rev: bool = False) -> float:
        f = ((i + 0.5) / n) if between else (i / max(1, n - 1))
        if rev:
            f = 1 - f
        return (py + ph - f * ph) if horiz else (px + f * pw)  # горизонтальные полосы: категория 0 внизу

    out: list[dict[str, Any]] = []
    for g, sers in groups:
        gt = ln(g)
        if gt not in ("barChart", "lineChart"):
            continue
        axids = xp(g, "./c:axId/@val")
        vaid = next((a for a in axids if a in scales), None)
        caid = next((a for a in axids if a in ch.axes and a not in scales), None)
        if vaid is None:
            continue
        cat_rev = caid is not None and val(ch.axes[caid].find(C + "scaling"), "orientation", "minMax") == "maxMin"
        between = scales[vaid]["crossBetween"] != "midCat" or gt == "barChart"
        grouping = val(g, "grouping", "clustered" if gt == "barChart" else "standard")
        stacked = grouping in ("stacked", "percentStacked")
        n = max(len(s["vals"]) for s in sers) if sers else 0
        if n == 0:
            continue
        cat_len = (ph if horiz else pw) / n
        bw = total = 0.0
        ov = 0.0
        if gt == "barChart":
            gap = float(val(g, "gapWidth", "150")) / 100.0
            ov = float(val(g, "overlap", "100" if stacked else "0")) / 100.0
            k = 1 if stacked else len(sers)
            bw = cat_len / (k - (k - 1) * ov + gap)
            total = bw * (k - (k - 1) * ov)
        sc = scales[vaid]
        base_v = min(max(0.0, sc["min"]), sc["max"])
        pos_acc, neg_acc = [0.0] * n, [0.0] * n
        pct_tot = [sum(abs(s["vals"][i] or 0.0) for s in sers if i < len(s["vals"])) for i in range(n)]
        for j, s in enumerate(sers):
            eff = [g.find(C + "dLbls"), s["el"].find(C + "dLbls")]
            for i in range(n):
                v = s["vals"][i] if i < len(s["vals"]) else None
                if v is None:
                    continue
                vv = v / pct_tot[i] if grouping == "percentStacked" and pct_tot[i] else v
                c = ccoord(i, n, between, cat_rev)
                a: float | None
                if gt == "barChart":
                    if stacked:
                        if vv >= 0:
                            a, b = pos_acc[i], pos_acc[i] + vv
                            pos_acc[i] = b
                        else:
                            a, b = neg_acc[i], neg_acc[i] + vv
                            neg_acc[i] = b
                        a = a if a != 0 else base_v
                        cc = c
                    else:
                        a, b = base_v, vv
                        cc = c - total / 2 + j * bw * (1 - ov) + bw / 2
                else:
                    if stacked:
                        pos_acc[i] += vv
                        b = pos_acc[i]
                    else:
                        b = vv
                    a, cc = None, c
                d = _point_dlbl(eff, i)
                if not d["show"]:
                    continue
                txt = _label_text(d, s, i, v, cats, locale)
                if not txt:
                    continue
                sz = d["sz"] or ch.sz
                lines = txt.split("\n")
                tw = max(text_width_in(t, sz, d["bold"], fscale) for t in lines)
                li, ti, ri, bi = d["ins"] or DEFAULT_INS
                w = tw + li + ri
                h = len(lines) * sz * 1.2 / 72.0 + ti + bi
                pos = d["pos"] or (
                    "outEnd" if (gt == "barChart" and not stacked) else ("ctr" if gt == "barChart" else "r")
                )
                vb = vcoord(vaid, b)
                if gt == "barChart":
                    assert a is not None
                    cx_, cy_ = _bar_label_pos(pos, cc, vcoord(vaid, a), vb, w, h, horiz)
                else:
                    msym = x1(s["el"], "./c:marker/c:symbol/@val")
                    msz = float(x1(s["el"], "./c:marker/c:size/@val") or 5)
                    mh = 0.0 if msym == "none" else msz / 2 / 72.0
                    x0, y0 = (vb, cc) if horiz else (cc, vb)
                    cx_, cy_ = _line_label_pos(pos, x0, y0, w, h, mh)
                ml = d["layout"]
                moved = False
                if ml:
                    if ml.get("xMode", "factor") == "edge" and ml.get("x") is not None:
                        cx_ = float(ml["x"]) * W + w / 2
                    elif ml.get("x") is not None:
                        cx_ += float(ml["x"]) * W
                    if ml.get("yMode", "factor") == "edge" and ml.get("y") is not None:
                        cy_ = float(ml["y"]) * H + h / 2
                    elif ml.get("y") is not None:
                        cy_ += float(ml["y"]) * H
                    moved = True
                box = (ch.fx + cx_ - w / 2, ch.fy + cy_ - h / 2, ch.fx + cx_ + w / 2, ch.fy + cy_ + h / 2)
                out.append(
                    {
                        "chart": ch.name,
                        "series": s["name"],
                        "kind": "bar" if gt == "barChart" else "line",
                        "idx": i,
                        "value": v,
                        "text": txt,
                        "pos": pos,
                        "manual": moved,
                        "axis": vaid,
                        "box": [round(z, 4) for z in box],
                    }
                )
    info = {
        "chart": ch.name,
        "frame": [ch.fx, ch.fy, W, H],
        "plot_inner_slide": [ch.fx + px, ch.fy + py, pw, ph],
        "plot_from": how,
        "axes": scales,
    }
    return out, info


def _plot_area(
    ch: Chart, scales: dict[str, dict[str, Any]], cats: list[str], horiz: bool, locale: str, fscale: float
) -> tuple[tuple[float, float, float, float], str]:
    W, H = ch.W, ch.H
    ml = _manual(ch.pa)
    left = right = bottom = top = 0.0
    for aid, s in scales.items():
        if s["deleted"] or s["tick_lbl"] == "none":
            continue
        sz = _txpr(ch.axes[aid])[0] or ch.sz
        if horiz:
            hgt = sz * 1.2 / 72 + AXIS_GAP
            if s["pos"] == "t":
                top += hgt
            else:
                bottom += hgt
        else:
            w = _axis_label_width(ch.axes[aid], s["min"], s["max"], s["step"], sz, fscale, locale, s["fmt_src"])
            if s["pos"] == "r":
                right += w + AXIS_GAP
            else:
                left += w + AXIS_GAP
    for aid, ax in ch.axes.items():
        if aid in scales or ln(ax) not in ("catAx", "dateAx"):
            continue
        if val(ax, "delete", "0") in ("1", "true") or val(ax, "tickLblPos", "nextTo") == "none":
            continue
        sz = _txpr(ax)[0] or ch.sz
        if horiz:
            left += max([text_width_in(c, sz, False, fscale) for c in cats] + [0.0]) + AXIS_GAP
        else:
            bottom += sz * 1.2 / 72 + AXIS_GAP
    if ml and ml.get("x") is not None and ml.get("w") is not None:
        x, y, w, h = (float(ml.get(k, 0)) for k in ("x", "y", "w", "h"))
        if ml.get("xMode", "factor") == "edge":
            X, Y, Wd, Hd = x * W, y * H, w * W, h * H
        else:
            est, _ = _estimate(ch, left, right, top, bottom, fscale)
            X, Y, Wd, Hd = est[0] + x * W, est[1] + y * H, w * W, h * H
        if ml.get("layoutTarget", "outer") == "outer":
            return (X + left, Y + top, Wd - left - right, Hd - top - bottom), "manualLayout(outer)"
        return (X, Y, Wd, Hd), "manualLayout(inner)"
    return _estimate(ch, left, right, top, bottom, fscale)


def _estimate(
    ch: Chart, left: float, right: float, top: float, bottom: float, fscale: float
) -> tuple[tuple[float, float, float, float], str]:
    W, H = ch.W, ch.H
    x0, y0, x1_, y1_ = CHART_PAD, CHART_PAD, W - CHART_PAD, H - CHART_PAD
    title = x1(ch.cs, "./c:chart/c:title")
    atd = x1(ch.cs, "./c:chart/c:autoTitleDeleted/@val")
    nser = sum(len(g.findall(C + "ser")) for g in ch.groups)
    if title is not None or (atd not in ("1", "true") and nser == 1):
        y0 += 14.0 * ch.sz / 10.0 * 1.2 / 72 + 0.1
    leg = x1(ch.cs, "./c:chart/c:legend")
    if leg is not None and val(leg, "overlay", "0") in ("0", "false"):
        lsz = _txpr(leg)[0] or ch.sz
        names = ["".join(xp(s, "./c:tx//c:v/text()")) for g in ch.groups for s in g.findall(C + "ser")]
        pos = val(leg, "legendPos", "r")
        if pos in ("r", "l"):
            lw = max([text_width_in(n, lsz, False, fscale) for n in names] + [0.0]) + LEGEND_KEY + 0.1
            if pos == "r":
                x1_ -= lw
            else:
                x0 += lw
        else:
            rows, cur = 1, 0.0
            for wd in (text_width_in(n, lsz, False, fscale) + LEGEND_KEY for n in names):
                if cur + wd > (x1_ - x0) and cur > 0:
                    rows, cur = rows + 1, 0.0
                cur += wd
            lh = rows * lsz * 1.2 / 72 + 0.1
            if pos == "b":
                y1_ -= lh
            else:
                y0 += lh
    X, Y = x0 + left, y0 + top
    return (X, Y, (x1_ - right) - X, (y1_ - bottom) - Y), "estimate"


def _flag(el: Any, tag: str) -> bool | None:
    v = val(el, tag)
    return None if v is None else v in ("1", "true")


def _point_dlbl(eff: list[Any], i: int) -> dict[str, Any]:
    gd, sd = eff
    pd = None
    if sd is not None:
        for p in sd.findall(C + "dLbl"):
            if val(p, "idx") == str(i):
                pd = p
    chain = [e for e in (pd, sd, gd) if e is not None]
    d: dict[str, Any] = {
        "show": False,
        "pos": None,
        "fmt": None,
        "sz": None,
        "bold": False,
        "ins": None,
        "layout": None,
        "showSerName": False,
        "showCatName": False,
        "showPercent": False,
        "sep": None,
        "rich": None,
    }
    if (pd is not None and _flag(pd, "delete")) or (sd is not None and _flag(sd, "delete")):
        return d
    for key in ("showVal", "showSerName", "showCatName", "showPercent"):
        for e in chain:
            f = _flag(e, key)
            if f is not None:
                d[key] = f
                break
    d["show"] = any(d.get(k) for k in ("showVal", "showSerName", "showCatName", "showPercent"))
    for e in chain:
        if d["pos"] is None and val(e, "dLblPos"):
            d["pos"] = val(e, "dLblPos")
        nf = e.find(C + "numFmt")
        if d["fmt"] is None and nf is not None:
            d["fmt"] = (nf.get("formatCode"), nf.get("sourceLinked") in ("1", "true"))
        sz, b = _txpr(e)
        if d["sz"] is None and sz:
            d["sz"] = sz
            d["bold"] = bool(b)
        if d["ins"] is None:
            d["ins"] = _insets(e)
        if d["sep"] is None and e.find(C + "separator") is not None:
            d["sep"] = e.findtext(C + "separator")
    if pd is not None:
        d["layout"] = _manual(pd)
        r = xp(pd, "./c:tx/c:rich//a:t/text()")
        if r:
            d["rich"] = "".join(r)
    return d


def _label_text(d: dict[str, Any], s: dict[str, Any], i: int, v: float, cats: list[str], locale: str) -> str:
    if d["rich"]:
        return str(d["rich"])
    parts = []
    if d.get("showSerName"):
        parts.append(s["name"])
    if d.get("showCatName"):
        parts.append(cats[i] if i < len(cats) else "")
    if d.get("showVal"):
        code = s["fmt"]
        if d["fmt"] and not d["fmt"][1]:
            code = d["fmt"][0]
        parts.append(format_value(v, code, locale))
    return str(d["sep"] if d["sep"] is not None else ", ").join(parts)


def _bar_label_pos(pos: str, c: float, va: float, vb: float, w: float, h: float, horiz: bool) -> tuple[float, float]:
    if horiz:
        sgn = 1 if vb >= va else -1
        half = w / 2
        if pos == "inEnd":
            x = vb - sgn * (half + LBL_GAP)
        elif pos == "inBase":
            x = va + sgn * (half + LBL_GAP)
        elif pos == "outEnd":
            x = vb + sgn * (half + LBL_GAP)
        else:
            x = (va + vb) / 2
        return x, c
    sgn = -1 if vb <= va else 1  # -1: столбец растёт вверх (y уменьшается)
    half = h / 2
    if pos == "inEnd":
        y = vb - sgn * (half + LBL_GAP)
    elif pos == "inBase":
        y = va + sgn * (half + LBL_GAP)
    elif pos == "outEnd":
        y = vb + sgn * (half + LBL_GAP)
    else:
        y = (va + vb) / 2
    return c, y


def _line_label_pos(pos: str, x: float, y: float, w: float, h: float, mh: float) -> tuple[float, float]:
    g = mh + LBL_GAP
    if pos == "t":
        return x, y - g - h / 2
    if pos == "b":
        return x, y + g + h / 2
    if pos == "l":
        return x - g - w / 2, y
    if pos == "ctr":
        return x, y
    return x + g + w / 2, y  # r (по умолчанию)


# --- наложения ---------------------------------------------------------------------------------


def inter(a: Any, b: Any) -> float:
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    return float(ix * iy) if ix > 0 and iy > 0 else 0.0


def find_pairs(
    labels: list[dict[str, Any]],
    kinds: tuple[str, ...] = ("line-bar", "line-line"),
    min_area: float = 0.0005,
    min_frac: float = 0.06,
    margin: float = 0.0,
) -> list[dict[str, Any]]:
    """Пары наложенных подписей: ``overlap`` — пересечение не меньше ``min_frac`` меньшей
    подписи (на отрисовке это заметно), иначе ``near``. ``margin`` (дюймы) раздувает каждую
    подпись: запас на неточность модели."""

    def grow(b: Any) -> tuple[float, float, float, float]:
        return b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin

    out = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            la, lb = labels[i], labels[j]
            kind = "-".join(sorted((la["kind"], lb["kind"]), reverse=True))
            if kind not in kinds:
                continue
            ar = inter(grow(la["box"]), grow(lb["box"]))
            if ar <= min_area:
                continue
            sa = (la["box"][2] - la["box"][0]) * (la["box"][3] - la["box"][1])
            sb = (lb["box"][2] - lb["box"][0]) * (lb["box"][3] - lb["box"][1])
            fr = ar / min(sa, sb)
            out.append(
                {
                    "kind": kind,
                    "severity": "overlap" if fr >= min_frac else "near",
                    "area_in2": round(ar, 4),
                    "frac_of_smaller": round(fr, 3),
                    "a": _short(la),
                    "b": _short(lb),
                }
            )
    out.sort(key=lambda p: -p["area_in2"])
    return out


def _short(lb: dict[str, Any]) -> dict[str, Any]:
    return {k: lb[k] for k in ("chart", "series", "idx", "text", "kind", "manual")}
