"""Блок ``table_fill``: данные из набора в готовую таблицу слайда-образца (ARCHITECTURE.md,
раздел 6.5).

Заголовок — из столбцов набора, новые строки копируются с первой строки данных, новые
столбцы — с последнего столбца, с новыми id строк и столбцов. Общая ширина таблицы не
меняется: первые столбцы (``fixed_columns``) сохраняют ширину, остальные делят оставшуюся
поровну. Стиль таблицы и границы сохраняются, размер шрифта задаётся явно. Если таблица не
помещается выше нижней границы, шрифт уменьшается ступенями; если не помещается и с самым
мелким, остаются последние строки (самые свежие), сколько поместится, с предупреждением.

Пример::

    - type: table_fill
      shape: 3
      dataset: cohorts
      columns:
        - {column: quarter, header: Квартал}
        - {column: base, header: База, decimals: 0}
        - {column: q1, header: "1", percent: true, decimals: 0}
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
from autogenerator.contracts.ooxml import A
from autogenerator.contracts.theme import EMU_PER_INCH

from .formats import fmt_date
from .textwidth import text_width_in
from .values import ValueFormat, one_line

A16 = "http://schemas.microsoft.com/office/drawing/2014/main"
DEFAULT_MAR_LR = 91440  # поля ячейки по умолчанию, EMU
DEFAULT_MAR_TB = 45720
LINE = 1.2  # межстрочный интервал в долях кегля


class TableColumn(ValueFormat):
    column: str
    header: str | None = Field(None, description="Текст заголовка; пусто — id столбца")
    date_format: str = Field("LLL yyyy", description="Формат дат (Babel): «янв. 2026»")

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
    header: bool = Field(True, description="Первая строка таблицы — заголовок")
    fixed_columns: int = Field(1, ge=0, description="Сколько первых столбцов сохраняют ширину шаблона")
    font_size: float | None = Field(None, gt=0, description="Кегль, пт; пусто — как в первой ячейке данных шаблона")
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

    def _rows(self, params: Any, data: BlockData) -> tuple[list[str], list[list[str]]]:
        if params.dataset not in data.datasets:
            raise BlockError([f"нет набора «{params.dataset}»"])
        table = data.datasets[params.dataset]
        cols = list(params.columns or [])
        if params.other_columns if params.other_columns is not None else not cols:
            listed = {c.column for c in cols}
            fmt = params.number.model_dump()
            cols += [TableColumn(column=c, header=_header(c), **fmt) for c in table.column_names if c not in listed]
        missing = [c.column for c in cols if c.column not in table.column_names]
        if missing:
            have = ", ".join(table.column_names)
            raise BlockError([f"в наборе «{params.dataset}» нет столбцов {', '.join(missing)} (есть: {have})"])
        data_cols = [table.column(c.column).to_pylist() for c in cols]
        rows = [
            [_cell_text(v, c, params.empty) for v, c in zip(values, cols, strict=True)]
            for values in zip(*data_cols, strict=True)
        ]
        if params.max_rows and len(rows) > params.max_rows:
            rows = rows[-params.max_rows :]
        return [c.header or c.column for c in cols], rows

    def render(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> None:
        shape = target.shape
        if shape is None or not getattr(shape, "has_table", False):
            raise BlockError(["table_fill заполняет таблицу слайда-образца"])
        header, rows = self._rows(params, data)
        if not rows:
            ctx.warn(f"в наборе «{params.dataset}» нет строк: таблица выводится с одной пустой строкой")
            rows = [[params.empty] * len(header)]
        tbl = shape.table._tbl
        trs = list(tbl.tr_lst)
        size = params.font_size or _run_size(trs[1] if params.header and len(trs) > 1 else trs[0]) or 12.0
        _set_widths(tbl, len(header), params.fixed_columns)
        widths = [int(gc.get("w")) for gc in tbl.tblGrid.findall(f"{A}gridCol")]

        body_rows = rows
        slide_h = target.slide.part.package.presentation_part.presentation.slide_height
        bottom = int(params.bottom * EMU_PER_INCH) if params.bottom else int(slide_h) - EMU_PER_INCH // 4
        top = int(shape.top)
        while True:
            _fill(tbl, header if params.header else None, body_rows, size)
            heights = [_row_height(tr, widths, size) for tr in tbl.tr_lst]
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

    def preview(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> PreviewSpec | None:
        header, rows = self._rows(params, data)
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
