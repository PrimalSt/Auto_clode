"""Блок ``table_fill``: данные из набора в готовую таблицу слайда-образца (ARCHITECTURE.md,
раздел 6.5).

Заголовок — из столбцов набора, новые строки копируются с первой строки данных, новые
столбцы — с последнего столбца, с новыми id строк и столбцов. Общая ширина таблицы не
меняется: первые столбцы (``fixed_columns``) сохраняют ширину, остальные делят оставшуюся
поровну. Стиль таблицы и границы сохраняются, размер шрифта задаётся явно: из параметров,
из ячеек шаблона, иначе унаследованный (стиль прочего текста образца слайдов, стиль
презентации, 18 пт — как в PowerPoint). Строки не ниже, чем в шаблоне (новые — как строка,
с которой скопированы); если так таблица не помещается выше нижней границы, высота строк
считается по тексту, затем шрифт уменьшается ступенями; если не помещается и с самым
мелким, остаются последние строки (самые свежие), сколько поместится, с предупреждением.

Заливка ячеек данных: цвет «RRGGBB» из столбца набора (``fill`` у столбца) или тепловая
карта (``heatmap``) — цветовая шкала по значениям, как условное форматирование Excel. Цвет
из столбца важнее карты; ячейки без цвета сохраняют заливку шаблона.

Пример::

    - type: table_fill
      shape: 3
      dataset: cohorts
      columns:
        - {column: quarter, header: Квартал}
        - {column: base, header: База, decimals: 0}
        - {column: q1, header: "1", percent: true, decimals: 0}
      heatmap: {columns: [q1], min: 0, max: 1}
"""

from __future__ import annotations

import copy
import html
import math
import random
import re
from datetime import date, datetime
from typing import Any

from lxml import etree
from pptx.util import Emu
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autogenerator.contracts import (
    BlockContext,
    BlockData,
    BlockError,
    BlockPlugin,
    BlockTarget,
    BlockTargetKind,
    DataNeeds,
    PreviewKind,
    PreviewSpec,
    TemplateSlideInfo,
)
from autogenerator.contracts.ooxml import A, P
from autogenerator.contracts.theme import EMU_PER_INCH

from .formats import fmt_date
from .textwidth import text_width_in
from .values import ValueFormat, one_line

A16 = "http://schemas.microsoft.com/office/drawing/2014/main"
DEFAULT_MAR_LR = 91440  # поля ячейки по умолчанию, EMU
DEFAULT_MAR_TB = 45720
LINE = 1.2  # межстрочный интервал в долях кегля
DEFAULT_SIZE = 18.0  # кегль PowerPoint, если ни образец слайдов, ни презентация его не задают
HEX = re.compile(r"[0-9A-F]{6}")
# Порядок a:tcPr (CT_TableCellProperties): границы и cell3D, затем заливка, затем headers и extLst.
BEFORE_FILL = {f"{A}{t}" for t in ("lnL", "lnR", "lnT", "lnB", "lnTlToBr", "lnBlToTr", "cell3D")}
FILLS = {f"{A}{t}" for t in ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")}


def _rgb(v: Any) -> str | None:
    """Цвет «RRGGBB» (можно с «#»); пусто — ``None``, не цвет — ``ValueError``."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    text = str(v).strip().lstrip("#").upper()
    if not HEX.fullmatch(text):
        raise ValueError(f"не цвет RRGGBB: «{v}»")
    return text


def _number(v: Any) -> float | None:
    """Значение ячейки тепловой карты: число набора или текст вида «45,0 %»; иначе ``None``."""
    if isinstance(v, str):
        try:
            v = float("".join(v.split()).replace("%", "").replace(",", "."))
        except ValueError:
            return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        return None
    return float(v)


class Heatmap(BaseModel):
    """Тепловая карта: цвета ``colors`` равномерно от ``min`` до ``max``, между соседними —
    линейно по каналам (с отбрасыванием дробной части), за пределами — крайние цвета."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(min_length=1, description="Столбцы набора, выводимые в таблице")
    min: float | None = Field(None, description="Значение первого цвета; пусто — наименьшее в ячейках карты")
    max: float | None = Field(None, description="Значение последнего цвета; пусто — наибольшее в ячейках карты")
    colors: list[str] = Field(
        ["F8696B", "FFEB84", "63BE7B"], min_length=2, description="Цвета шкалы RRGGBB: красный, жёлтый, зелёный"
    )

    @field_validator("colors")
    @classmethod
    def _hex(cls, v: list[str]) -> list[str]:
        out = [_rgb(c) for c in v]
        if None in out:
            raise ValueError("пустой цвет шкалы")
        return [c for c in out if c]

    @model_validator(mode="after")
    def _range(self) -> Heatmap:
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError("min должен быть меньше max")
        return self

    def color(self, value: float, lo: float, hi: float) -> str:
        if hi > lo:
            ratio = min(max((value - lo) / (hi - lo), 0.0), 1.0)
        else:  # min не меньше max: например, все значения карты равны
            ratio = 0.0 if value < lo else 1.0 if value > hi else 0.5
        n = len(self.colors)
        segment = min(int(ratio * (n - 1)), n - 2)
        local = min(max((ratio - segment / (n - 1)) * (n - 1), 0.0), 1.0)
        c0, c1 = (bytes.fromhex(c) for c in self.colors[segment : segment + 2])
        return "".join(f"{int(a + local * (b - a)):02X}" for a, b in zip(c0, c1, strict=True))


class TableColumn(ValueFormat):
    column: str
    header: str | None = Field(None, description="Текст заголовка; пусто — id столбца")
    date_format: str = Field("LLL yyyy", description="Формат дат (Babel): «янв. 2026»")
    fill: str | None = Field(None, description="Столбец набора с цветом заливки ячейки RRGGBB; пусто — как в шаблоне")

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, v: Any) -> Any:
        return {"column": v} if isinstance(v, str) else v


class TableFillParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    columns: list[TableColumn] | None = Field(None, description="Столбцы по порядку; пусто — все столбцы набора")
    other_columns: bool | None = Field(
        None,
        description="Добавить после перечисленных остальные столбцы набора (например, месяцы сводной таблицы); "
        "по умолчанию — да, если columns не задан",
    )
    number: ValueFormat = Field(default_factory=ValueFormat, description="Формат чисел в остальных столбцах")
    heatmap: Heatmap | None = Field(None, description="Тепловая карта: заливка ячеек по их значениям")
    header: bool = Field(True, description="Первая строка таблицы — заголовок")
    fixed_columns: int = Field(1, ge=0, description="Сколько первых столбцов сохраняют ширину шаблона")
    font_size: float | None = Field(
        None, gt=0, description="Кегль, пт; пусто — как в ячейках шаблона, иначе унаследованный"
    )
    min_font_size: float = Field(8, gt=0)
    bottom: float | None = Field(None, gt=0, description="Нижняя граница таблицы, дюймов от верха слайда")
    max_rows: int | None = Field(None, ge=1, description="Не больше стольких строк данных (последних)")
    empty: str = Field("", description="Текст пустой ячейки")


def _cell_text(v: Any, col: TableColumn, empty: str) -> str:
    if v is None:
        return empty
    if isinstance(v, datetime | date):
        return fmt_date(v, col.date_format) or ""
    if isinstance(v, int | float) and not isinstance(v, bool):
        return col.format(v) or empty
    return one_line(str(v))


def _header(name: str, pattern: str = "LLL yyyy") -> str:
    """Заголовок столбца по его имени: месяцы сводной таблицы («2026-01») — «янв. 2026»."""
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", name)
    if m:
        try:
            return fmt_date(date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)), pattern) or name
        except ValueError:
            pass
    return name


def _new_id() -> str:
    return str(random.randint(10**8, 2**31 - 1))


def _renew_ids(el: Any, tag: str) -> None:
    for e in el.iter(f"{{{A16}}}{tag}"):
        e.set("val", _new_id())


def _run_size(tc: Any) -> float | None:
    for rpr in tc.iter(f"{A}rPr", f"{A}endParaRPr"):
        if rpr.get("sz"):
            return int(rpr.get("sz")) / 100
    return None


def _inherited_size(slide: Any) -> float:
    """Кегль текста ячеек без явного размера: стиль прочего текста образца слайдов, затем
    стиль презентации по умолчанию, иначе 18 пт."""
    master = slide.slide_layout.slide_master._element
    pres = slide.part.package.presentation_part._element
    for rpr in (
        master.find(f"{P}txStyles/{P}otherStyle/{A}lvl1pPr/{A}defRPr"),
        pres.find(f"{P}defaultTextStyle/{A}lvl1pPr/{A}defRPr"),
    ):
        if rpr is not None and rpr.get("sz"):
            return int(rpr.get("sz")) / 100
    return DEFAULT_SIZE


def set_cell(tc: Any, text: str, size: float) -> None:
    """Текст ячейки с оформлением её первого прогона и явным кеглем."""
    body = tc.find(f"{A}txBody")
    if body is None:
        body = etree.SubElement(tc, f"{A}txBody")
        etree.SubElement(body, f"{A}bodyPr")
        etree.SubElement(body, f"{A}lstStyle")
    paras = body.findall(f"{A}p")
    if not paras:
        paras = [etree.SubElement(body, f"{A}p")]
    p = paras[0]
    for extra in paras[1:]:
        body.remove(extra)
    runs = p.findall(f"{A}r")
    end = p.find(f"{A}endParaRPr")
    if runs:
        run = runs[0]
        for r in runs[1:]:
            p.remove(r)
    else:
        run = etree.Element(f"{A}r")
        rpr = etree.SubElement(run, f"{A}rPr")
        if end is not None:
            for k, v in end.attrib.items():
                rpr.set(k, v)
            for child in end:
                rpr.append(copy.deepcopy(child))
        etree.SubElement(run, f"{A}t")
        if end is not None:
            end.addprevious(run)
        else:
            p.append(run)
    for other in p.findall(f"{A}fld") + p.findall(f"{A}br"):
        p.remove(other)
    rpr = run.find(f"{A}rPr")
    if rpr is None:
        rpr = etree.Element(f"{A}rPr")
        run.insert(0, rpr)
    rpr.set("sz", str(round(size * 100)))
    if any("А" <= ch <= "я" or ch in "Ёё" for ch in text):
        rpr.set("lang", "ru-RU")
    for attr in ("err", "dirty"):
        rpr.attrib.pop(attr, None)
    run.find(f"{A}t").text = text
    if end is not None:
        end.set("sz", str(round(size * 100)))


def set_fill(tc: Any, rgb: str) -> None:
    """Сплошная заливка ячейки вместо прежней, на своём месте в ``a:tcPr``."""
    pr = tc.find(f"{A}tcPr")
    if pr is None:
        pr = etree.Element(f"{A}tcPr")
        ext = tc.find(f"{A}extLst")
        if ext is not None:
            ext.addprevious(pr)
        else:
            tc.append(pr)
    for el in list(pr):
        if el.tag in FILLS:
            pr.remove(el)
    at = max((i + 1 for i, el in enumerate(pr) if el.tag in BEFORE_FILL), default=0)
    fill = etree.Element(f"{A}solidFill")
    etree.SubElement(fill, f"{A}srgbClr", val=rgb)
    pr.insert(at, fill)


def _fills(
    cols: list[TableColumn], data_cols: list[list[Any]], fill_cols: list[list[Any] | None], heatmap: Heatmap | None
) -> list[list[str | None]]:
    """Заливки ячеек по строкам данных: цвет из столбца ``fill``, иначе тепловой карты;
    ``None`` — как в шаблоне. Наименьшее и наибольшее значение карты — по всем строкам, и тем,
    что потом не поместятся на слайд: цвет ячейки не зависит от кегля."""
    nrows = len(data_cols[0]) if data_cols else 0
    out: list[list[str | None]] = [[None] * len(cols) for _ in range(nrows)]
    if heatmap is not None:
        nums = {
            (i, j): x
            for j, c in enumerate(cols)
            if c.column in heatmap.columns
            for i, v in enumerate(data_cols[j])
            if (x := _number(v)) is not None
        }
        if nums:
            lo = min(nums.values()) if heatmap.min is None else heatmap.min
            hi = max(nums.values()) if heatmap.max is None else heatmap.max
            for (i, j), x in nums.items():
                out[i][j] = heatmap.color(x, lo, hi)
    for j, (c, values) in enumerate(zip(cols, fill_cols, strict=True)):
        for i, v in enumerate(values or []):
            try:
                rgb = _rgb(v)
            except ValueError as e:
                raise BlockError([f"в столбце заливки «{c.fill}» {e}"]) from None
            if rgb:
                out[i][j] = rgb
    return out


def _margins(tc: Any) -> tuple[int, int]:
    pr = tc.find(f"{A}tcPr")
    lr = tb = 0
    for k, d in (("marL", DEFAULT_MAR_LR), ("marR", DEFAULT_MAR_LR)):
        lr += int(pr.get(k, d)) if pr is not None else d
    for k, d in (("marT", DEFAULT_MAR_TB), ("marB", DEFAULT_MAR_TB)):
        tb += int(pr.get(k, d)) if pr is not None else d
    return lr, tb


def _row_height(tr: Any, widths: list[int], size: float) -> int:
    """Высота строки в EMU: строки текста по ширине столбцов и поля ячеек."""
    best = 0
    for tc, w in zip(tr.findall(f"{A}tc"), widths, strict=False):
        lr, tb = _margins(tc)
        text = "".join(t.text or "" for t in tc.iter(f"{A}t"))
        room = max(w - lr, 1) / EMU_PER_INCH
        lines = max(1, math.ceil(text_width_in(text, size) / room)) if text else 1
        best = max(best, int(lines * size * LINE / 72 * EMU_PER_INCH) + tb)
    return best


class TableFillBlock(BlockPlugin):
    name = "table_fill"
    title = "Таблица слайда-образца"
    Params = TableFillParams
    target_kind = BlockTargetKind.TABLE

    def data_needs(self, params: Any) -> DataNeeds:
        return DataNeeds(datasets={params.dataset})

    def check(self, params: Any, example: TemplateSlideInfo | None, shape_id: int | None) -> list[str]:
        if example is None or shape_id is None:
            return ["table_fill заполняет таблицу слайда-образца: укажите shape — id таблицы"]
        if example.table(shape_id) is None:
            ids = ", ".join(f"{t.shape_id} «{t.shape_name}»" for t in example.tables) or "нет"
            return [f"на слайде нет таблицы с id {shape_id} (таблицы: {ids})"]
        return []

    def _rows(self, params: Any, data: BlockData) -> tuple[list[str], list[list[str]], list[list[str | None]]]:
        """Заголовок, тексты строк и заливки их ячеек (``None`` — как в шаблоне)."""
        if params.dataset not in data.datasets:
            raise BlockError([f"нет набора «{params.dataset}»"])
        table = data.datasets[params.dataset]
        cols = list(params.columns or [])
        if params.other_columns if params.other_columns is not None else not cols:
            listed = {c.column for c in cols} | {c.fill for c in cols if c.fill}
            fmt = params.number.model_dump()
            cols += [TableColumn(column=c, header=_header(c), **fmt) for c in table.column_names if c not in listed]
        needed = [c.column for c in cols] + [c.fill for c in cols if c.fill]
        missing = [c for c in needed if c not in table.column_names]
        if missing:
            have = ", ".join(table.column_names)
            raise BlockError([f"в наборе «{params.dataset}» нет столбцов {', '.join(missing)} (есть: {have})"])
        shown = [c.column for c in cols]
        stray = [c for c in params.heatmap.columns if c not in shown] if params.heatmap else []
        if stray:
            raise BlockError([f"heatmap: столбцов {', '.join(stray)} нет в таблице (есть: {', '.join(shown)})"])
        data_cols = [table.column(c.column).to_pylist() for c in cols]
        fill_cols = [table.column(c.fill).to_pylist() if c.fill else None for c in cols]
        if params.max_rows and table.num_rows > params.max_rows:
            data_cols = [v[-params.max_rows :] for v in data_cols]
            fill_cols = [v[-params.max_rows :] if v is not None else None for v in fill_cols]
        rows = [
            [_cell_text(v, c, params.empty) for v, c in zip(values, cols, strict=True)]
            for values in zip(*data_cols, strict=True)
        ]
        return [c.header or c.column for c in cols], rows, _fills(cols, data_cols, fill_cols, params.heatmap)

    def render(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> None:
        shape = target.shape
        if shape is None or not getattr(shape, "has_table", False):
            raise BlockError(["table_fill заполняет таблицу слайда-образца"])
        header, rows, fills = self._rows(params, data)
        if not rows:
            ctx.warn(f"в наборе «{params.dataset}» нет строк: таблица выводится с одной пустой строкой")
            rows = [[params.empty] * len(header)]
        tbl = shape.table._tbl
        trs = list(tbl.tr_lst)
        size = (
            params.font_size
            or _run_size(trs[1] if params.header and len(trs) > 1 else trs[0])
            or _run_size(tbl)
            or _inherited_size(target.slide)
        )
        _set_widths(tbl, len(header), params.fixed_columns)
        widths = [int(gc.get("w")) for gc in tbl.tblGrid.findall(f"{A}gridCol")]

        body_rows = rows
        slide_h = target.slide.part.package.presentation_part.presentation.slide_height
        bottom = int(params.bottom * EMU_PER_INCH) if params.bottom else int(slide_h) - EMU_PER_INCH // 4
        top = int(shape.top)
        while True:
            _fill(tbl, header if params.header else None, body_rows, size)
            heights = [_row_height(tr, widths, size) for tr in tbl.tr_lst]
            # Строки не ниже, чем в шаблоне (новые — как строка, с которой скопированы), если
            # так таблица помещается; иначе — по тексту, и только потом шрифт мельче.
            design = [max(h, int(tr.get("h", 0))) for tr, h in zip(tbl.tr_lst, heights, strict=True)]
            if top + sum(design) <= bottom:
                heights = design
                break
            if top + sum(heights) <= bottom:
                break
            if size - 1 >= params.min_font_size:
                size -= 1
                continue
            if len(body_rows) <= 1:
                ctx.warn("таблица не помещается выше нижней границы даже с одной строкой")
                break
            body_rows = body_rows[1:]
        if len(body_rows) < len(rows):
            ctx.warn(
                f"таблица не поместилась: показаны последние {len(body_rows)} строк из {len(rows)} (кегль {size:g} пт)"
            )
        for tr, h in zip(tbl.tr_lst, heights, strict=True):
            tr.set("h", str(h))
        shape.height = Emu(sum(heights))
        # Заливки — у показанных строк: отброшенные сверху уходят вместе со своими цветами.
        shown = fills[len(rows) - len(body_rows) :]
        for tr, row_fills in zip(list(tbl.tr_lst)[1 if params.header else 0 :], shown, strict=False):
            for tc, rgb in zip(tr.findall(f"{A}tc"), row_fills, strict=False):
                if rgb:
                    set_fill(tc, rgb)

    def preview(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> PreviewSpec | None:
        header, rows, _ = self._rows(params, data)
        head = "".join(f"<th>{html.escape(h)}</th>" for h in header)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows[:50])
        return PreviewSpec(
            kind=PreviewKind.HTML, html=f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
        )


def _set_widths(tbl: Any, ncols: int, fixed_columns: int) -> None:
    """Столбцов в таблице — ``ncols``. Если число столбцов не меняется, ширины шаблона
    остаются как есть. Иначе новые столбцы копируются с последнего, лишние удаляются с конца;
    общая ширина сохраняется, первые ``fixed_columns`` сохраняют ширину, остальные делят
    оставшееся поровну."""
    grid = tbl.tblGrid
    gcs = grid.findall(f"{A}gridCol")
    if len(gcs) == ncols:
        return
    total = sum(int(gc.get("w")) for gc in gcs)
    for gc in gcs[ncols:]:
        grid.remove(gc)
    for tr in tbl.tr_lst:
        for tc in tr.findall(f"{A}tc")[ncols:]:
            tr.remove(tc)
    for _ in range(ncols - len(gcs)):
        last = grid.findall(f"{A}gridCol")[-1]
        new = copy.deepcopy(last)
        _renew_ids(new, "colId")
        last.addnext(new)
        for tr in tbl.tr_lst:
            tcs = tr.findall(f"{A}tc")
            new_tc = copy.deepcopy(tcs[-1])
            for attr in ("gridSpan", "hMerge", "vMerge", "rowSpan"):
                new_tc.attrib.pop(attr, None)
            tcs[-1].addnext(new_tc)
    gcs = grid.findall(f"{A}gridCol")
    fixed = min(fixed_columns, ncols - 1)
    fixed_w = sum(int(gc.get("w")) for gc in gcs[:fixed])
    if fixed_w >= total:
        fixed, fixed_w = 0, 0
    n_rest = ncols - fixed
    rest = (total - fixed_w) // n_rest
    for i, gc in enumerate(gcs[fixed:]):
        gc.set("w", str(rest if i < n_rest - 1 else total - fixed_w - rest * (n_rest - 1)))


def _fill(tbl: Any, header: list[str] | None, rows: list[list[str]], size: float) -> None:
    """Строк в таблице — заголовок и ``rows``: новые копируются с первой строки данных."""
    trs = list(tbl.tr_lst)
    first_data = 1 if header is not None else 0
    pattern = trs[first_data] if len(trs) > first_data else trs[-1]
    want = first_data + len(rows)
    for tr in trs[want:]:
        tbl.remove(tr)
    trs = list(tbl.tr_lst)
    while len(trs) < want:
        new = copy.deepcopy(pattern)
        _renew_ids(new, "rowId")
        trs[-1].addnext(new)
        trs.append(new)
    trs = list(tbl.tr_lst)
    if header is not None:
        for tc, text in zip(trs[0].findall(f"{A}tc"), header, strict=False):
            set_cell(tc, text, size)
    for tr, row in zip(trs[first_data:], rows, strict=False):
        for tc, text in zip(tr.findall(f"{A}tc"), row, strict=False):
            set_cell(tc, text, size)
