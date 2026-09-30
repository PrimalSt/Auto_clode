"""Блок «график»: новая «родная» диаграмма PowerPoint на слайде из макета. Данные встраиваются
в файл, график можно править в PowerPoint (ARCHITECTURE.md, раздел 6.5).

Категории передаются готовыми строками в русском формате («янв. 2026»), а не датами: иначе
на оси было бы видно «2026-01-01» или серийное число.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.util import Emu, Pt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autogenerator.contracts import BlockContext, BlockData, BlockPlugin, BlockTarget, DataNeeds

from .formats import fmt_date

CHART_TYPES = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "stacked_column": XL_CHART_TYPE.COLUMN_STACKED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "stacked_bar": XL_CHART_TYPE.BAR_STACKED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
}
ONE_SERIES_ONLY = {"pie", "doughnut"}
BARS = {"column", "stacked_column", "bar", "stacked_bar"}
HORIZONTAL = {"bar", "stacked_bar"}


class SeriesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    name: str | None = Field(None, description="Название в легенде; пусто — id столбца")
    number_format: str | None = Field(None, description="Формат Excel, например #,##0 или 0.0%")

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, v: Any) -> Any:
        return {"column": v} if isinstance(v, str) else v


class ChartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart: Literal["column", "stacked_column", "bar", "stacked_bar", "line", "pie", "doughnut"] = "column"
    dataset: str
    x: str = Field(description="Столбец набора с категориями")
    series: list[SeriesSpec] = Field(min_length=1)
    title: str | None = None
    data_labels: bool = False
    number_format: str = Field("#,##0", description="Формат значений и подписей по умолчанию")
    legend: bool | None = Field(None, description="Пусто — легенда, если серий больше одной")
    category_format: str = Field("LLL yyyy", description="Формат дат в категориях (Babel): «янв. 2026»")
    font_size: float | None = Field(None, gt=0)
    max_categories: int = Field(36, ge=1, description="Больше категорий — предупреждение")

    @model_validator(mode="after")
    def _check(self) -> ChartParams:
        if self.chart in ONE_SERIES_ONLY and len(self.series) != 1:
            raise ValueError(f"у диаграммы {self.chart} ровно одна серия")
        return self


def category_text(v: Any, pattern: str) -> str:
    if v is None:
        return "—"
    if isinstance(v, datetime | date):
        return fmt_date(v, pattern) or ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _number(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _value_axis_at_max_category(chart: Any) -> None:
    """Ось значений пересекает ось категорий на последней категории (``c:crosses val="max"``
    у ``c:valAx``). В python-pptx для этого нет свойства, поэтому правим XML."""
    for crosses in chart._chartSpace.xpath(".//c:valAx/c:crosses"):
        crosses.set("val", "max")


class ChartBlock(BlockPlugin):
    name = "chart"
    title = "График"
    Params = ChartParams

    def data_needs(self, params: Any) -> DataNeeds:
        return DataNeeds(datasets={params.dataset})

    def render(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> None:
        table = data.datasets[params.dataset]
        names = table.column_names
        for col in [params.x, *(s.column for s in params.series)]:
            if col not in names:
                raise ValueError(f"в наборе «{params.dataset}» нет столбца «{col}» (есть: {', '.join(names)})")
        cats = [category_text(v, params.category_format) for v in table.column(params.x).to_pylist()]
        if not cats:
            raise ValueError(f"набор «{params.dataset}» пуст — графику нечего показать")
        if len(cats) > params.max_categories:
            ctx.warn(
                f"на графике {len(cats)} категорий — слайд будет трудно читать; "
                f"добавьте в набор top: {params.max_categories}"
            )
        cd = CategoryChartData(number_format=params.number_format)
        cd.categories = cats
        all_values: list[float | None] = []
        for s in params.series:
            values = [_number(v) for v in table.column(s.column).to_pylist()]
            all_values += values
            cd.add_series(s.name or s.column, values, number_format=s.number_format)

        g = target.geometry
        frame = target.slide.shapes.add_chart(CHART_TYPES[params.chart], Emu(g.x), Emu(g.y), Emu(g.cx), Emu(g.cy), cd)
        chart = frame.chart
        if params.title:
            chart.has_title = True
            chart.chart_title.text_frame.text = params.title
        else:
            chart.has_title = False
        legend = (
            params.legend if params.legend is not None else (len(params.series) > 1 or params.chart in ONE_SERIES_ONLY)
        )
        chart.has_legend = legend
        if legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False
        if params.font_size:
            chart.font.size = Pt(params.font_size)
        if params.chart in BARS and all(v is None or v >= 0 for v in all_values):
            # Столбцы от нуля: иначе PowerPoint может начать ось с 21 млн, и разница
            # между столбцами покажется в разы больше настоящей.
            chart.value_axis.minimum_scale = 0
        if params.chart in HORIZONTAL:
            # PowerPoint рисует полосы снизу вверх; первая категория набора должна быть сверху.
            # После разворота ось значений уезжает наверх — возвращаем её вниз.
            chart.category_axis.reverse_order = True
            _value_axis_at_max_category(chart)
        if params.data_labels:
            plot = chart.plots[0]
            plot.has_data_labels = True
            labels = plot.data_labels
            if params.chart in ONE_SERIES_ONLY:
                labels.show_percentage = True
                labels.show_value = False
                labels.number_format = "0%"
            else:
                labels.show_value = True
                labels.number_format = params.series[0].number_format or params.number_format
            labels.number_format_is_linked = False
