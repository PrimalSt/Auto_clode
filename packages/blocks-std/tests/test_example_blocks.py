"""Блоки слайдов-образцов на синтетическом шаблоне examples/templates/synthetic.pptx."""

from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pytest
from pptx import Presentation
from pptx.util import Inches, Pt

from autogenerator.blocks_std.chart_fill import ChartFillBlock
from autogenerator.blocks_std.formats import NBSP
from autogenerator.blocks_std.markers import MarkersBlock
from autogenerator.blocks_std.table_fill import TableFillBlock
from autogenerator.contracts import BlockData, BlockError, BlockTarget, Geometry, Period
from autogenerator.contracts.ooxml import A, C, all_text
from autogenerator.theme.slides import slide_info

TEMPLATE = Path(__file__).parents[3] / "examples" / "templates" / "synthetic.pptx"


@dataclass
class Ctx:
    period: Period = field(default_factory=lambda: Period.parse("2026-03"))
    scenario_name: str = "Тест"
    preview: bool = False
    warnings: list[str] = field(default_factory=list)
    kept: list[tuple[int, str]] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def keep_marker(self, shape_id: int, name: str) -> None:
        self.kept.append((shape_id, name))


def example(number: int, prs=None):
    prs = prs or Presentation(str(TEMPLATE))
    slide = prs.slides[number - 1]
    return prs, slide, slide_info(slide, number, slide.slide_id, "")


def shape_target(slide, info, shape_id: int) -> BlockTarget:
    shape = next(s for s in slide.shapes if s.shape_id == shape_id)
    return BlockTarget(slide=slide, geometry=Geometry(x=0, y=0, cx=1, cy=1), shape=shape, example=info)


def slide_target(slide, info) -> BlockTarget:
    return BlockTarget(slide=slide, geometry=Geometry(x=0, y=0, cx=1, cy=1), example=info)


def text_slide(*paragraphs: list[str]):
    """Слайд с одной надписью: каждый абзац — список прогонов (метка разрезана, как в PowerPoint)."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2))
    tf = box.text_frame
    for i, runs in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        for j, text in enumerate(runs):
            r = p.add_run()
            r.text = text
            r.font.size = Pt(20 + j)
            r.font.bold = j == 1
    return slide, box, slide_info(slide, 1, slide.slide_id, "")


def run(block, target, params: dict, data: BlockData | None = None, ctx: Ctx | None = None) -> Ctx:
    ctx = ctx or Ctx()
    block.render(target, block.parse_params(params), data or BlockData(), ctx)
    return ctx


# --- метки --------------------------------------------------------------------------------

COMMON = {"Месяц": "period.month", "Год": "period.year"}


def test_markers_fill_example_slide():
    _, slide, info = example(2)
    assert set(info.marker_names) == {"Месяц", "Год", "Выручка", "Прирост+", "Доля"}
    params = {
        "bindings": {
            "Выручка": {"metric": "revenue", "scale": "млн", "decimals": 1},
            "{{Прирост+}}": {"metric": "growth", "percent": True, "sign": True},
            "Доля": {"metric": "share", "percent": True, "decimals": 0},
        },
        "common": COMMON,
    }
    block = MarkersBlock()
    assert block.check(block.parse_params(params), info, None) == []
    data = BlockData(metrics={"revenue": 26_229_878, "growth": 0.125, "share": 0.4})
    run(block, slide_target(slide, info), params, data)
    text = "\n".join(all_text(s._element) for s in slide.shapes)
    assert "{{" not in text
    assert "Итоги, март 2026" in text
    assert "26,2" in text and "+12,5%" in text and "40%" in text


def test_marker_split_into_runs_keeps_formatting():
    slide, box, info = text_slide(["Рост за ", "{{Ме", "сяц}}", " — {{Доля}} %"])
    block = MarkersBlock()
    run(
        block,
        slide_target(slide, info),
        {"bindings": {"Доля": {"metric": "x", "percent": True}}, "common": {"Месяц": "period.month_prep"}},
        BlockData(metrics={"x": 0.0567}),
    )
    p = box.text_frame.paragraphs[0]
    # «%» после метки в шаблоне: значение подставляется без своего знака
    assert p.text == "Рост за марте — 5,7 %"
    runs = p.runs
    value = next(r for r in runs if r.text == "марте")
    assert value.font.size == Pt(21) and value.font.bold  # оформление прогона, где начиналась метка
    assert value._r.find(f"{A}rPr").get("lang") == "ru-RU"


def test_marker_binding_precedence():
    slide, box, info = text_slide(["{{X}} и {{X}}"], ["ещё {{X}}"])
    shape = box.shape_id
    params = {
        "bindings": {"X": {"value": "слайд"}, f"X@{shape}#2": {"value": "второе"}},
        "common": {"X": {"value": "общее"}},
    }
    run(MarkersBlock(), slide_target(slide, info), params)
    assert [p.text for p in box.text_frame.paragraphs] == ["слайд и второе", "ещё слайд"]


def test_marker_empty_value_behaviours():
    def go(empty: str, preview: bool = False) -> tuple[str, Ctx]:
        slide, box, info = text_slide(["Итого: {{Сумма}}"])
        ctx = Ctx(preview=preview)
        params = {"bindings": {"Сумма": {"metric": "total", "empty": empty}}}
        run(MarkersBlock(), slide_target(slide, info), params, BlockData(metrics={"total": None}), ctx)
        return box.text_frame.text, ctx

    with pytest.raises(BlockError, match="нет значения"):
        go("stop")
    assert go("blank")[0] == "Итого: "
    text, ctx = go("keep")
    assert text == "Итого: {{Сумма}}" and ctx.kept and not ctx.warnings
    text, ctx = go("stop", preview=True)
    assert text == "Итого: {{Сумма}}" and ctx.kept and ctx.warnings


def test_markers_check_reports_unbound_and_stray_bindings():
    _, _, info = example(2)
    block = MarkersBlock()
    problems = block.check(block.parse_params({"bindings": {"Нет такой": "x"}, "common": COMMON}), info, None)
    assert any("{{Выручка}} не привязана" in p for p in problems)
    assert any("нет метки для привязки «Нет такой»" in p for p in problems)
    with pytest.raises(ValueError, match="без фигуры"):
        block.parse_params({"common": {"X@5": "x"}})


def test_unreplaceable_marker_stops_build():
    _, slide, info = example(7)
    block = MarkersBlock()
    params = block.parse_params({"common": COMMON})
    problems = block.check(params, info, None)
    assert any("перенос строки" in p for p in problems)
    with pytest.raises(BlockError):
        block.render(slide_target(slide, info), params, BlockData(), Ctx())


# --- графики ------------------------------------------------------------------------------


def chart_xml(slide, shape_id: int):
    shape = next(s for s in slide.shapes if s.shape_id == shape_id)
    return shape.chart._chartSpace


def series_by_group(cs) -> list[int]:
    plot = cs.find(f"{C}chart/{C}plotArea")
    return [len(g.findall(f"{C}ser")) for g in plot if g.tag.endswith("Chart")]


MONTHS = pa.table(
    {
        "month": ["янв", "фев", "мар", "апр"],
        "a": [10.0, 12.0, 14.0, 13.0],
        "b": [5.0, 6.0, 5.5, 7.0],
        "c": [3.0, 2.0, 4.0, 5.0],
        "share": [0.31, 0.35, 0.33, 0.36],
    }
)


def test_chart_fill_combo_changes_categories_and_series():
    _, slide, info = example(2)
    block = ChartFillBlock()
    params = {
        "dataset": "m",
        "categories": "month",
        "series": ["a", "b", "c", {"column": "share", "name": "Доля", "number_format": "0%"}],
        "groups": [3, 1],
    }
    assert block.check(block.parse_params(params), info, 6) == []
    t = shape_target(slide, info, 6)
    run(block, t, params, BlockData(datasets={"m": MONTHS}))
    cs = chart_xml(slide, 6)
    assert series_by_group(cs) == [3, 1]
    idx = [int(s.get("val")) for s in cs.iter(f"{C}idx") if s.getparent().tag == f"{C}ser"]
    assert len(set(idx)) == 4
    chart = t.shape.chart
    assert list(chart.plots[0].categories) == ["янв", "фев", "мар", "апр"]
    assert [s.name for p in chart.plots for s in p.series] == ["a", "b", "c", "Доля"]
    colors = [
        s.find(f"{C}spPr/{A}solidFill/{A}srgbClr").get("val")
        for s in cs.iter(f"{C}ser")
        if s.find(f"{C}spPr/{A}solidFill/{A}srgbClr") is not None
    ]
    assert len(colors) == len(set(colors))
    # Формат из сценария получают и подписи: здесь общие подписи группы линии (одна серия);
    # у столбцов формат задан не у всех серий — остаётся формат шаблона.
    label_formats = [g.find(f"{C}dLbls/{C}numFmt").get("formatCode") for g in cs.iter(f"{C}barChart", f"{C}lineChart")]
    assert label_formats == ["#,##0.0", "0%"]
    params["series"][3]["number_format"] = "0.0%"
    run(block, t, params, BlockData(datasets={"m": MONTHS}))
    assert chart_xml(slide, 6).find(f".//{C}lineChart/{C}dLbls/{C}numFmt").get("formatCode") == "0.0%"
    ctx = Ctx()
    block.finish_slide(slide, [(t, block.parse_params(params))], ctx)


def test_chart_fill_series_count_needs_groups():
    _, _, info = example(2)
    block = ChartFillBlock()
    problems = block.check(block.parse_params({"dataset": "m", "categories": "month", "series": ["a", "b"]}), info, 6)
    assert problems and "groups" in problems[0]
    assert (
        "нет графика с id 99"
        in block.check(block.parse_params({"dataset": "m", "categories": "month", "series": ["a"]}), info, 99)[0]
    )


def test_pie_collects_others():
    _, slide, info = example(3)
    regions = pa.table({"r": [f"Регион {i}" for i in range(9)], "v": [float(9 - i) for i in range(9)]})
    t = shape_target(slide, info, 4)
    ctx = run(
        ChartFillBlock(), t, {"dataset": "r", "categories": "r", "series": ["v"]}, BlockData(datasets={"r": regions})
    )
    cats = list(t.shape.chart.plots[0].categories)
    assert len(cats) == 6 and cats[-1] == "Прочие"
    assert list(t.shape.chart.plots[0].series[0].values)[-1] == 1 + 2 + 3 + 4
    assert any("Прочие" in w for w in ctx.warnings)


def test_labels_per_category_fix_category_count():
    _, slide, info = example(4)
    assert info.chart(3).labels_per_category
    four = pa.table({"k": ["а", "б", "в", "г"], "x": [0.5] * 4, "y": [0.5] * 4})
    with pytest.raises(BlockError, match="закреплены"):
        run(
            ChartFillBlock(),
            shape_target(slide, info, 3),
            {"dataset": "d", "categories": "k", "series": ["x", "y"]},
            BlockData(datasets={"d": four}),
        )
    five = pa.table({"k": list("абвгд"), "x": [0.6] * 5, "y": [0.4] * 5})
    run(
        ChartFillBlock(),
        shape_target(slide, info, 3),
        {"dataset": "d", "categories": "k", "series": ["x", "y"]},
        BlockData(datasets={"d": five}),
    )


def test_overlaid_charts_need_same_categories():
    _, slide, info = example(5)
    block = ChartFillBlock()
    a = pa.table({"k": ["1", "2", "3"], "x": [1.0, 2, 3], "y": [1.0, 1, 1], "z": [0.1, 0.2, 0.3]})
    b = pa.table({"k": ["1", "2", "4"], "x": [1.0, 2, 3], "y": [1.0, 1, 1], "z": [0.1, 0.2, 0.3]})
    data = BlockData(datasets={"a": a, "b": b})
    p5 = {"dataset": "a", "categories": "k", "series": ["x", "y"]}
    p6 = {"dataset": "b", "categories": "k", "series": ["x", "y", "z"]}
    t5, t6 = shape_target(slide, info, 5), shape_target(slide, info, 6)
    run(block, t5, p5, data)
    run(block, t6, p6, data)
    with pytest.raises(BlockError, match="наложены"):
        block.finish_slide(slide, [(t5, block.parse_params(p5)), (t6, block.parse_params(p6))], Ctx())


# --- таблица ------------------------------------------------------------------------------


def table_shape(slide):
    return next(s for s in slide.shapes if s.has_table)


def test_table_fill_rows_and_columns():
    _, slide, info = example(6)
    shape = table_shape(slide)
    width = shape.width
    grid_before = [c.width for c in shape.table.columns]
    data = pa.table(
        {
            "region": [f"Регион {i}" for i in range(8)],
            "plan": [1_250_000.0 + i for i in range(8)],
            "share": [0.1 * i for i in range(8)],
        }
    )
    params = {
        "dataset": "t",
        "columns": [
            {"column": "region", "header": "Регион"},
            {"column": "plan", "header": "План, млн", "scale": "million", "decimals": 1},
            {"column": "share", "header": "Доля", "percent": True, "decimals": 0},
        ],
    }
    block = TableFillBlock()
    assert block.check(block.parse_params(params), info, shape.shape_id) == []
    ctx = run(block, shape_target(slide, info, shape.shape_id), params, BlockData(datasets={"t": data}))
    tbl = shape.table
    assert len(tbl.rows) == 9 and len(tbl.columns) == 3
    assert [tbl.cell(0, j).text for j in range(3)] == ["Регион", "План, млн", "Доля"]
    assert [tbl.cell(2, j).text for j in range(3)] == ["Регион 1", "1,3", "10%"]
    assert sum(c.width for c in tbl.columns) == pytest.approx(width, abs=3)
    assert tbl.columns[0].width == grid_before[0]  # первый столбец сохраняет ширину
    row_ids = [e.get("val") for e in tbl._tbl.iter("{http://schemas.microsoft.com/office/drawing/2014/main}rowId")]
    assert len(row_ids) == len(set(row_ids))
    assert not ctx.warnings


def test_table_fill_shrinks_then_keeps_last_rows():
    _, slide, info = example(6)
    shape = table_shape(slide)
    data = pa.table({"k": [f"строка {i}" for i in range(80)], "v": [float(i) for i in range(80)]})
    ctx = run(
        TableFillBlock(),
        shape_target(slide, info, shape.shape_id),
        {"dataset": "t", "min_font_size": 9},
        BlockData(datasets={"t": data}),
    )
    tbl = shape.table
    assert len(tbl.rows) < 81
    assert tbl.cell(len(tbl.rows) - 1, 0).text == "строка 79"  # остаются последние строки
    assert any("показаны последние" in w for w in ctx.warnings)
    sizes = {r.get("sz") for r in tbl._tbl.iter(f"{A}rPr")}
    assert sizes == {"900"}
    assert shape.top + shape.height <= slide.part.package.presentation_part.presentation.slide_height


def test_table_fill_unknown_column():
    _, slide, info = example(6)
    shape = table_shape(slide)
    with pytest.raises(BlockError, match="нет столбцов nope"):
        run(
            TableFillBlock(),
            shape_target(slide, info, shape.shape_id),
            {"dataset": "t", "columns": ["nope"]},
            BlockData(datasets={"t": pa.table({"a": [1]})}),
        )


def test_number_nbsp_in_table_cells():
    _, slide, info = example(6)
    shape = table_shape(slide)
    run(
        TableFillBlock(),
        shape_target(slide, info, shape.shape_id),
        {"dataset": "t"},
        BlockData(datasets={"t": pa.table({"k": ["x"], "v": [1234567.0]})}),
    )
    assert shape.table.cell(1, 1).text == f"1{NBSP}234{NBSP}567"


def test_series_labels_get_scenario_format():
    from lxml import etree

    from autogenerator.blocks_std.chart_xml import set_label_formats

    ser = (
        '<c:ser><c:idx val="{i}"/><c:order val="{i}"/><c:dLbls>{dlbl}<c:numFmt formatCode="#,##0" sourceLinked="0"/>'
        '<c:showVal val="1"/></c:dLbls></c:ser>'
    )
    dlbl = '<c:dLbl><c:idx val="0"/><c:numFmt formatCode="#,##0" sourceLinked="0"/><c:showVal val="1"/></c:dLbl>'
    cs = etree.fromstring(
        f'<c:chartSpace xmlns:c="{C[1:-1]}"><c:chart><c:plotArea><c:barChart>'
        + ser.format(i=0, dlbl=dlbl)
        + ser.format(i=1, dlbl="")
        + "</c:barChart></c:plotArea></c:chart></c:chartSpace>"
    )
    set_label_formats(cs, ["#,##0;-#,##0;", None])
    codes = [nf.get("formatCode") for nf in cs.iter(f"{C}numFmt")]
    assert codes == ["#,##0;-#,##0;", "#,##0;-#,##0;", "#,##0"]  # подпись точки, подписи серии, вторая серия


def test_point_unique_ids_shared_with_labels_but_not_between_points():
    from lxml import etree

    from autogenerator.blocks_std.chart_xml import C16, check_chart, cleanup_points

    def ext(uid: str) -> str:
        return f'<c:extLst><c:ext uri="{{C3BC}}"><c16:uniqueId val="{uid}"/></c:ext></c:extLst>'

    # Так круговую с настройками секторов сохраняет PowerPoint: у сектора и его подписи один id.
    dpt = '<c:dPt><c:idx val="{i}"/><c:spPr><a:solidFill><a:srgbClr val="{rgb}"/></a:solidFill></c:spPr>{ext}</c:dPt>'
    dlbl = '<c:dLbl><c:idx val="{i}"/><c:showVal val="1"/>{ext}</c:dLbl>'
    points = [("{00000001-AA}", "FF0000"), ("{00000003-AA}", "00FF00")]
    cs = etree.fromstring(
        f'<c:chartSpace xmlns:c="{C[1:-1]}" xmlns:a="{A[1:-1]}" xmlns:c16="{C16}"><c:chart><c:plotArea><c:pieChart>'
        '<c:ser><c:idx val="0"/><c:order val="0"/>'
        + "".join(dpt.format(i=i, rgb=rgb, ext=ext(u)) for i, (u, rgb) in enumerate(points))
        + "<c:dLbls>"
        + "".join(dlbl.format(i=i, ext=ext(u)) for i, (u, _) in enumerate(points))
        + "</c:dLbls>"
        + '<c:val><c:numRef><c:numCache><c:ptCount val="2"/></c:numCache></c:numRef></c:val>'
        + ext("{0000000C-AA}")
        + "</c:ser></c:pieChart></c:plotArea></c:chart></c:chartSpace>"
    )
    assert check_chart(cs, 2) == []
    cleanup_points(cs, 4, {})  # секторов стало больше: новые копируют оформление, но не id
    uids = [u.get("val") for d in cs.iter(f"{C}dPt") for u in d.iter(f"{{{C16}}}uniqueId")]
    assert len(uids) == 4 and len(set(uids)) == 4
    for el in cs.iter(f"{C}ptCount"):
        el.set("val", "4")
    assert check_chart(cs, 4) == []
