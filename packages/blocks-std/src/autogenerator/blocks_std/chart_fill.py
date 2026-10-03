"""Блок ``chart_fill``: данные из набора в готовый график слайда-образца (F-419;
ARCHITECTURE.md, раздел 6.5).

Устройство графика задаёт шаблон: цвета, подписи, оси, комбинированная структура (столбцы и
линия на второй оси), легенда и стиль сохраняются. Для каждой серии задаются столбец набора,
название для легенды и формат чисел; серии перечисляются в порядке шаблона: группы в порядке
документа (сначала столбцы, потом линия), внутри группы — по порядку серий.

Пример::

    - type: chart_fill
      shape: 6                      # id графика на слайде шаблона
      dataset: by_month
      categories: month             # столбец с категориями
      series:
        - {column: online, name: Онлайн}
        - {column: offline, name: Офлайн}
        - {column: share_online, name: Доля онлайн, number_format: "0%"}

Число серий по умолчанию — как в шаблоне. Другое число задаётся ``groups`` (сколько серий в
каждой группе, например ``[3, 1]``). Число категорий может меняться, кроме графиков, рядом с
которыми надписи расставлены под каждую категорию. Круговая диаграмма получает не больше
секторов, чем в шаблоне: остальные собираются в последний сектор «Прочие».

Серии из данных (``series_from`` вместо ``series``) — для графика с одной группой серий, не
круговой. Набор «длинный»: строка на категорию и серию; серий столько, сколько разных названий
в столбце ``column``, как в сводной таблице::

    - type: chart_fill
      shape: 6
      dataset: regs_by_channel      # строки: месяц, канал, число регистраций
      categories: month
      series_from: {column: channel, value: regs, order: name}

Категории — в порядке появления в наборе, пропущенная пара категории и серии — 0, повторы
складываются, строки без названия серии пропускаются. Порядок серий ``order``: ``name`` — по
названию, ``data`` — по первому появлению, список названий — сначала они, потом остальные по
названию. Новые серии получают свои цвета, лишние серии шаблона удаляются.
"""

from __future__ import annotations

from typing import Any, Literal

from pptx.chart.data import CategoryChartData
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autogenerator.contracts import (
    BlockContext,
    BlockData,
    BlockError,
    BlockPlugin,
    BlockTarget,
    BlockTargetKind,
    ChartInfo,
    DataNeeds,
    PreviewKind,
    PreviewSpec,
    TemplateSlideInfo,
)

from .chart import category_text
from .chart_xml import check_chart, cleanup_points, set_label_formats, set_series_counts, theme_colors
from .formats import SCALES, Scale
from .label_bands import fix_slide_labels
from .values import SCALE_ALIASES

PIES = {"pie", "pie3D", "doughnut", "ofPie"}
PERCENT_TOLERANCE = 0.02


class FillSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="Столбец набора со значениями")
    name: str | None = Field(None, description="Название в легенде; пусто — id столбца")
    number_format: str | None = Field(
        None, description="Формат Excel для данных и подписей: #,##0, 0.0%, …; пусто — как у серии в шаблоне"
    )
    scale: Scale | None = Field(None, description="Разделить значения: thousand, million, billion")

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, v: Any) -> Any:
        return {"column": v} if isinstance(v, str) else v

    @field_validator("scale", mode="before")
    @classmethod
    def _scale_alias(cls, v: Any) -> Any:
        return SCALE_ALIASES.get(v.strip().lower(), v) if isinstance(v, str) else v


class SeriesFrom(BaseModel):
    """Серии из данных: по одной на каждое название в столбце ``column``."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="Столбец набора с названиями серий")
    value: str = Field(description="Столбец набора со значениями")
    order: Literal["name", "data"] | list[str] = Field(
        "name",
        description="Порядок серий: name — по названию, data — по первому появлению в наборе, список названий — "
        "сначала они, потом остальные по названию",
    )
    number_format: str | None = Field(
        None, description="Формат Excel для данных и подписей всех серий; пусто — как у серий в шаблоне"
    )
    scale: Scale | None = Field(None, description="Разделить значения: thousand, million, billion")

    @field_validator("scale", mode="before")
    @classmethod
    def _scale_alias(cls, v: Any) -> Any:
        return SCALE_ALIASES.get(v.strip().lower(), v) if isinstance(v, str) else v


class ChartFillParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    categories: str = Field(description="Столбец набора с категориями")
    series: list[FillSeries] | None = Field(None, min_length=1, description="Серии по столбцам набора")
    series_from: SeriesFrom | None = Field(None, description="Серии из данных вместо series")
    groups: list[int] | None = Field(None, description="Сколько серий в каждой группе графика, например [3, 1]")
    category_format: str = Field("LLL yyyy", description="Формат дат в категориях (Babel): «янв. 2026»")
    expected_categories: list[str] | None = Field(
        None, description="Ожидаемые категории по порядку (для графиков с надписями под каждую категорию)"
    )
    max_points: int | None = Field(None, ge=2, description="Секторов круговой не больше; пусто — как в шаблоне")
    others: str = Field("Прочие", description="Название сектора для всего, что не поместилось")
    labels: Literal["auto", "off"] = Field(
        "auto", description="auto — развести подписи линии и столбцов на комбинированных графиках"
    )

    @model_validator(mode="after")
    def _check(self) -> ChartFillParams:
        if (self.series is None) == (self.series_from is None):
            raise ValueError("у chart_fill должен быть ровно один источник серий: series или series_from")
        if self.series_from is not None and self.groups is not None:
            raise ValueError("groups не задаётся вместе с series_from: число серий берётся из данных")
        return self


def _number(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _scaled(col: list[float | None], scale: Scale | None) -> list[float | None]:
    if not scale:
        return col
    div = SCALES[scale][0]
    return [None if v is None else v / div for v in col]


def _layout(params: Any, chart: ChartInfo, n: int) -> list[int]:
    """Сколько серий в каждой группе графика; ``n`` — сколько серий задано (или пришло из данных)."""
    if params.groups is not None:
        if len(params.groups) != len(chart.groups):
            raise BlockError([f"в графике {len(chart.groups)} групп серий, а в groups — {len(params.groups)}"])
        if sum(params.groups) != n:
            raise BlockError([f"в groups {sum(params.groups)} серий, а в series — {n}"])
        return list(params.groups)
    counts = chart.series_count
    if sum(counts) == n:
        return counts
    if len(counts) == 1:
        return [n]
    raise BlockError(
        [
            f"в графике шаблона серий {' + '.join(map(str, counts))} по группам, а задано {n}: "
            "укажите groups, сколько серий в каждой группе"
        ]
    )


def _check_series_from(chart: ChartInfo) -> None:
    """Серии из данных — только у графика с одной группой серий, не круговой: иначе неясно, в
    какую группу попадёт новая серия."""
    if len(chart.groups) != 1:
        raise BlockError(
            [
                f"series_from — только для графика с одной группой серий, а в графике шаблона групп "
                f"{len(chart.groups)}: перечислите серии в series"
            ]
        )
    if chart.groups[0].kind in PIES:
        raise BlockError(["у круговой диаграммы одна серия: series_from для неё не подходит, задайте series"])


def _series_order(names: list[str], order: str | list[str]) -> list[str]:
    """Порядок серий: ``name`` — по названию (по кодам символов, как сводная таблица pandas),
    ``data`` — в порядке появления, список — сначала перечисленные названия, которые есть в
    данных, потом остальные по названию."""
    if order == "data":
        return names
    by_name = sorted(names)
    if order == "name":
        return by_name
    first = [n for n in dict.fromkeys(order) if n in names]
    return first + [n for n in by_name if n not in first]


def _pivot(
    params: Any, table: Any, cats: list[str]
) -> tuple[list[FillSeries], list[str], list[list[float | None]], list[str]]:
    """Длинный набор (категория, серия, значение) → серии, категории, значения серий и
    предупреждения. Категории — в порядке появления, пропущенная пара категории и серии — 0,
    повторы складываются, строки без названия серии пропускаются."""
    src = params.series_from
    raw = table.column(src.column).to_pylist()
    nums = [_number(v) for v in table.column(src.value).to_pylist()]
    sums: dict[tuple[str, str], float] = {}
    order: dict[str, None] = {}
    names: dict[str, None] = {}
    skipped = 0
    for cat, r, v in zip(cats, raw, nums, strict=True):
        name = "" if r is None else category_text(r, params.category_format)
        if not name.strip():
            skipped += 1
            continue
        order.setdefault(cat)
        names.setdefault(name)
        sums[cat, name] = sums.get((cat, name), 0.0) + (v or 0.0)
    if not names:
        raise BlockError([f"в наборе «{params.dataset}» нет названий серий: столбец «{src.column}» пуст"])
    ordered = _series_order(list(names), src.order)
    specs = [FillSeries(column=src.value, name=n, number_format=src.number_format, scale=src.scale) for n in ordered]
    values = [_scaled([sums.get((c, n), 0.0) for c in order], src.scale) for n in ordered]
    notes = [f"в наборе «{params.dataset}» пропущены строки без названия серии: {skipped}"] if skipped else []
    return specs, list(order), values, notes


class ChartFillBlock(BlockPlugin):
    name = "chart_fill"
    title = "График слайда-образца"
    Params = ChartFillParams
    target_kind = BlockTargetKind.CHART

    def data_needs(self, params: Any) -> DataNeeds:
        return DataNeeds(datasets={params.dataset})

    def check(self, params: Any, example: TemplateSlideInfo | None, shape_id: int | None) -> list[str]:
        if example is None or shape_id is None:
            return ["chart_fill заполняет график слайда-образца: укажите shape — id графика"]
        chart = example.chart(shape_id)
        if chart is None:
            ids = ", ".join(f"{c.shape_id} «{c.shape_name}»" for c in example.charts) or "нет"
            return [f"на слайде нет графика с id {shape_id} (графики: {ids})"]
        try:
            if params.series_from is not None:
                _check_series_from(chart)
            else:
                _layout(params, chart, len(params.series))
        except BlockError as e:
            return e.problems
        if params.series is not None and chart.groups[0].kind in PIES and len(params.series) != 1:
            return ["у круговой диаграммы одна серия"]
        want = params.expected_categories
        if chart.labels_per_category and want is not None and len(want) != chart.categories:
            return [f"рядом с графиком надписи под {chart.categories} категорий, а в expected_categories {len(want)}"]
        return []

    # --- данные -------------------------------------------------------------------------

    def _table(
        self, params: Any, data: BlockData, chart: ChartInfo
    ) -> tuple[list[FillSeries], list[str], list[list[float | None]], list[str]]:
        """Серии (заданные или из данных), категории, значения серий и предупреждения."""
        src = params.series_from
        if src is not None:
            _check_series_from(chart)
        if params.dataset not in data.datasets:
            raise BlockError([f"нет набора «{params.dataset}»"])
        table = data.datasets[params.dataset]
        names = table.column_names
        columns = [src.column, src.value] if src is not None else [s.column for s in params.series]
        missing = [c for c in [params.categories, *columns] if c not in names]
        if missing:
            raise BlockError(
                [f"в наборе «{params.dataset}» нет столбцов {', '.join(missing)} (есть: {', '.join(names)})"]
            )
        cats = [category_text(v, params.category_format) for v in table.column(params.categories).to_pylist()]
        if not cats:
            raise BlockError([f"набор «{params.dataset}» пуст — графику нечего показать"])
        notes: list[str] = []
        if src is not None:
            specs, cats, values, notes = _pivot(params, table, cats)
            have = sum(chart.series_count)
            if len(specs) != have:
                notes.append(
                    f"серий в наборе «{params.dataset}» {len(specs)}, а в графике шаблона {have}: "
                    + ("новые серии получают свои цвета" if len(specs) > have else "лишние серии шаблона удалены")
                )
        else:
            specs = list(params.series)
            values = [_scaled([_number(v) for v in table.column(s.column).to_pylist()], s.scale) for s in specs]
        kind = chart.groups[0].kind if chart.groups else ""
        if kind in PIES:
            limit = params.max_points or chart.categories or len(cats)
            if len(cats) > limit:
                order = sorted(range(len(cats)), key=lambda i: -(values[0][i] or 0.0))
                keep = order[: limit - 1]
                rest = sum(values[0][i] or 0.0 for i in order[limit - 1 :])
                cats = [cats[i] for i in keep] + [params.others]
                values = [[values[0][i] for i in keep] + [rest]]
                notes.append(f"секторов больше {limit}: остальные собраны в «{params.others}»")
        if chart.labels_per_category:
            want = params.expected_categories
            if want is not None and cats != want:
                raise BlockError(
                    [f"рядом с графиком надписи под категории {', '.join(want)}, а набор даёт {', '.join(cats)}"]
                )
            if len(cats) != chart.categories:
                raise BlockError(
                    [
                        f"рядом с графиком надписи под {chart.categories} категорий, а в наборе "
                        f"«{params.dataset}» {len(cats)}: число и порядок категорий закреплены"
                    ]
                )
        return specs, cats, values, notes

    def _formats(self, specs: list[FillSeries], chart: ChartInfo, counts: list[int]) -> list[str]:
        """Формат чисел каждой серии: из сценария, из серии шаблона или формат подписей группы."""
        out = []
        k = 0
        for g, n in zip(chart.groups, counts, strict=True):
            for j in range(n):
                spec = specs[k]
                tpl = g.series[min(j, len(g.series) - 1)] if g.series else None
                out.append(spec.number_format or (tpl.number_format if tpl else None) or g.label_format or "General")
                k += 1
        return out

    def _check_values(
        self, chart: ChartInfo, counts: list[int], formats: list[str], values: list[list[float | None]]
    ) -> list[str]:
        notes = []
        k = 0
        for g, n in zip(chart.groups, counts, strict=True):
            pct_labels = bool(g.label_format and "%" in g.label_format)
            group_vals = values[k : k + n]
            for j in range(n):
                if (pct_labels or "%" in formats[k + j]) and any(
                    v is not None and abs(v) > 1 + PERCENT_TOLERANCE for v in group_vals[j]
                ):
                    notes.append(
                        f"серия {k + j + 1}: формат процентный, а значения больше 1 — ожидаются доли (0,25 = 25%)"
                    )
            if g.grouping == "percentStacked" and pct_labels and group_vals:
                for i in range(len(group_vals[0])):
                    total = sum((col[i] or 0.0) for col in group_vals)
                    if total and abs(total - 1) > PERCENT_TOLERANCE:
                        notes.append(
                            f"категория {i + 1}: сумма долей {total:.2f}, а подписи 100%-гистограммы показывают "
                            "сами значения"
                        )
                        break
            k += n
        return notes

    # --- сборка -------------------------------------------------------------------------

    def render(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> None:
        shape = target.shape
        example = target.example
        if shape is None or example is None or not getattr(shape, "has_chart", False):
            raise BlockError(["chart_fill заполняет график слайда-образца"])
        chart_info = example.chart(shape.shape_id)
        if chart_info is None:
            raise BlockError([f"на слайде шаблона нет графика с id {shape.shape_id}"])
        specs, cats, values, notes = self._table(params, data, chart_info)
        counts = _layout(params, chart_info, len(specs))
        formats = self._formats(specs, chart_info, counts)
        notes += self._check_values(chart_info, counts, formats, values)
        chart = shape.chart
        cs = chart._chartSpace
        theme = theme_colors(target.slide)
        if counts != chart_info.series_count:
            set_series_counts(cs, counts, theme)
        cd = CategoryChartData(number_format=formats[0])
        cd.categories = cats
        for spec, col, fmt in zip(specs, values, formats, strict=True):
            cd.add_series(spec.name or spec.column, col, number_format=fmt)
        try:
            chart.replace_data(cd)
        except Exception as e:  # тип графика, который python-pptx не умеет заполнять
            raise BlockError([f"python-pptx не заполнил график: {e}"]) from e
        cleanup_points(cs, len(cats), theme)
        set_label_formats(cs, [s.number_format for s in specs])
        problems = check_chart(cs, len(cats))
        if problems:
            raise BlockError(problems)
        for n in notes:
            ctx.warn(n)

    def finish_slide(self, slide: Any, items: list[tuple[BlockTarget, Any]], ctx: BlockContext) -> None:
        """После всех графиков слайда: одинаковые категории у наложенных графиков и подписи
        комбинированных графиков."""
        problems = []
        by_id = {t.shape.shape_id: t for t, _ in items if t.shape is not None}
        for t, _ in items:
            if t.example is None or t.shape is None:
                continue
            info = t.example.chart(t.shape.shape_id)
            if info is None:
                continue
            for other in info.overlaid:
                if other in by_id and other > info.shape_id:
                    a = list(t.shape.chart.plots[0].categories)
                    other_shape: Any = by_id[other].shape
                    b = list(other_shape.chart.plots[0].categories)
                    if a != b:
                        problems.append(
                            f"графики {info.shape_id} и {other} наложены друг на друга, а категории у них разные: "
                            f"{', '.join(map(str, a))} и {', '.join(map(str, b))}"
                        )
        if problems:
            raise BlockError(problems)
        # Разводятся подписи комбинированных графиков: линия с подписями над столбцами, в том
        # числе на наложенном графике. Остальным графикам разводить нечего.
        infos = {
            t.shape.shape_id: t.example.chart(t.shape.shape_id)
            for t, p in items
            if t.shape is not None and t.example is not None and p.labels == "auto"
        }
        lines = {sid for sid, info in infos.items() if info and any(g.kind == "line" and g.labels for g in info.groups)}
        ids = {sid for sid, info in infos.items() if info and (sid in lines or lines & set(info.overlaid))}
        if not ids:
            return
        for rec in fix_slide_labels(slide, only=ids):
            if rec.get("action") == "skip" and rec.get("reason"):
                ctx.warn(f"подписи графиков {', '.join(rec['cluster'])}: не разведены — {rec['reason']}")
            elif rec.get("action") == "warn":
                ctx.warn(f"подписи графиков {', '.join(rec['cluster'])}: {rec['reason']}")

    def preview(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> PreviewSpec | None:
        example = target.example
        shape_id = target.shape.shape_id if target.shape is not None else None
        info = example.chart(shape_id) if example is not None and shape_id is not None else None
        if info is None:
            return None
        specs, cats, values, notes = self._table(params, data, info)
        kind = info.groups[0].kind if info.groups else "bar"
        series = []
        k = 0
        for g, n in zip(info.groups, _layout(params, info, len(specs)), strict=True):
            for _ in range(n):
                spec = specs[k]
                typ = "pie" if g.kind in PIES else ("line" if g.kind == "line" else "bar")
                item: dict[str, Any] = {"name": spec.name or spec.column, "type": typ, "data": values[k]}
                if g.grouping in ("stacked", "percentStacked"):
                    item["stack"] = f"g{g.number}"
                if g.secondary:
                    item["yAxisIndex"] = 1
                if typ == "pie":
                    item["data"] = [{"name": c, "value": v} for c, v in zip(cats, values[k], strict=True)]
                series.append(item)
                k += 1
        option: dict[str, Any] = {"legend": {}, "tooltip": {}, "series": series}
        if kind not in PIES:
            horizontal = info.groups[0].direction == "bar"
            cat_axis = {"type": "category", "data": cats}
            value_axes = [{"type": "value"}] + ([{"type": "value"}] if any(g.secondary for g in info.groups) else [])
            option["xAxis"], option["yAxis"] = (value_axes, cat_axis) if horizontal else (cat_axis, value_axes)
        return PreviewSpec(kind=PreviewKind.ECHARTS, option=option, warnings=notes)
