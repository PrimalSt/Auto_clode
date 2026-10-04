"""Блок ``markers``: подстановка значений в метки ``{{…}}`` слайда-образца (F-418, F-420;
ARCHITECTURE.md, раздел 6.5).

Метка — буквальный ключ, а не выражение Jinja: ``{{lk+}}``, ``{{nps_lk=}}`` и ``{{+p}}`` работают
как есть. Привязка метки — показатель, переменная периода, текст с переменными (как у
текстового блока) или готовое значение, с форматом и поведением при пустом значении.
Привязки задаются на всю презентацию (``markers`` сценария), на слайд (``markers`` слайда:
``имя``), на одну фигуру (``имя@id фигуры``) или на одно вхождение (``имя@id фигуры#номер``);
действует самая точная.

Пример (сценарий)::

    markers:                         # на всю презентацию
      Месяц: period.month
      Год: period.year
    slides:
      - example: 257                 # id слайда шаблона
        markers:
          Выручка: {metric: revenue, scale: million, decimals: 1}
          Прирост+: {metric: revenue_prev_change_pct, percent: true, sign: true}
          Месяц@12#2: {period: month_dat}       # второе {{Месяц}} в фигуре 12: «к сентябрю»

Цвет значения по знаку — ``color``: ``sign`` — положительное зелёным, отрицательное красным,
ноль как в шаблоне; ``"1F4E79"`` — один цвет при любом знаке; полностью — ``positive``,
``negative``, ``zero`` и ``by`` (показатель, знак которого выбирает цвет). Без ``by`` знак
берётся из показателя привязки, у текста и готового значения (и если показатель пуст) — из
самого текста: «+» — положительное, «-» или «−» — отрицательное.

Замена: PowerPoint режет текст на прогоны по языку и флагу орфографии, поэтому текст абзаца
склеивается из прогонов, полей и переносов, метки ищутся в склеенном тексте и заменяются
справа налево. Значение получает отдельный прогон с оформлением прогона, где начинается
метка; язык — по значению (ru-RU для кириллицы), флаг ошибки орфографии снимается, ``color``
перекрашивает только этот прогон.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Literal

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autogenerator.contracts import (
    BlockContext,
    BlockData,
    BlockError,
    BlockPlugin,
    BlockTarget,
    BlockTargetKind,
    DataNeeds,
    MarkerInfo,
    PreviewKind,
    PreviewSpec,
    TemplateSlideInfo,
    short_form,
)
from autogenerator.contracts.ooxml import A, MarkerMatch, P, iter_markers, iter_shapes, paragraph_segments

from .formats import PeriodVars, quarter_label
from .text import referenced_metrics, render_text
from .values import ValueFormat, one_line

_KEY = re.compile(r"^(?P<name>.+?)@(?P<shape>\d+)(?:#(?P<occ>\d+))?$")
_CYRILLIC = re.compile("[А-Яа-яЁё]")
_LATIN = re.compile("[A-Za-z]")
_RGB = re.compile("^[0-9A-Fa-f]{6}$")
HIGHLIGHT = "FFFF00"


def _roman(q: int) -> str:
    return ["I", "II", "III", "IV"][q - 1]


PERIOD_VARS: dict[str, Any] = {
    "month": lambda v: v.month,
    "month_gen": lambda v: v.month_gen,
    "month_dat": lambda v: v.month_dat,
    "month_prep": lambda v: v.month_prep,
    "year": lambda v: str(v.year),
    "prev_year": lambda v: str(v.prev_year),
    "label": lambda v: v.label,
    "quarter": lambda v: str(v.quarter),
    "quarter_roman": lambda v: _roman(v.quarter),
    "quarter_name": lambda v: quarter_label(v.quarter_year, v.quarter).rsplit(" ", 1)[0],
    "quarter_label": lambda v: v.quarter_label,
    "quarter_year": lambda v: str(v.quarter_year),
    "last_quarter": lambda v: str(v.last_quarter),
    "last_quarter_roman": lambda v: _roman(v.last_quarter),
    "last_quarter_name": lambda v: quarter_label(v.last_quarter_year, v.last_quarter).rsplit(" ", 1)[0],
    "last_quarter_label": lambda v: v.last_quarter_label,
    "last_quarter_year": lambda v: str(v.last_quarter_year),
}
"""Переменные периода для меток. «Квартал отчётного периода» — тот, в который попадает конец
периода; «последний завершённый» — на конец отчётного периода (в отчёте за январь 2027 это
IV квартал 2026). ``*_name`` — «I квартал», ``*_label`` — «I квартал 2027»."""


def _rgb(v: Any) -> str:
    """``"1f4e79"`` или ``"#1F4E79"`` → ``"1F4E79"``."""
    if isinstance(v, int | float) and not isinstance(v, bool):
        # без кавычек YAML читает 123456 как число, а 2E7559 — как бесконечность
        raise ValueError(f'цвет прочитан как число ({v}): возьмите его в кавычки, например "2E7559"')
    s = str(v).strip().removeprefix("#")
    if not _RGB.match(s):
        raise ValueError(f"цвет «{v}» — не RGB: нужно 6 шестнадцатеричных цифр, например 2E7559")
    return s.upper()


class MarkerColor(BaseModel):
    """Цвет значения метки по знаку, RGB; пусто — цвет шаблона. Короткая запись: ``sign`` —
    цвета по умолчанию, ``"1F4E79"`` — один цвет при любом знаке."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=short_form({"type": ["string", "number"], "description": "sign или цвет RRGGBB"}),
    )

    positive: str | None = Field("2E7559", description="Цвет положительного значения; пусто — как в шаблоне")
    negative: str | None = Field("C00000", description="Цвет отрицательного значения; пусто — как в шаблоне")
    zero: str | None = Field(None, description="Цвет нуля; пусто — как в шаблоне")
    by: str | None = Field(
        None,
        description="id показателя, знак которого выбирает цвет; пусто — показатель привязки или знак в тексте",
    )

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() == "sign":
            return {}
        if isinstance(v, str | int | float) and not isinstance(v, bool):
            rgb = _rgb(v)
            return {"positive": rgb, "negative": rgb, "zero": rgb}
        return v

    @field_validator("positive", "negative", "zero", mode="before")
    @classmethod
    def _check_rgb(cls, v: Any) -> Any:
        return None if v is None else _rgb(v)

    def pick(self, sign: int) -> str | None:
        if sign > 0:
            return self.positive
        return self.negative if sign < 0 else self.zero


class MarkerBinding(ValueFormat):
    """Привязка метки. Короткая запись — строка: ``period.month`` — переменная периода,
    иначе — id показателя."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=short_form(
            {"type": ["string", "number"], "description": "id показателя, period.<переменная> или готовое значение"}
        ),
    )

    metric: str | None = None
    period: str | None = Field(None, description=f"Переменная периода: {', '.join(PERIOD_VARS)}")
    text: str | None = Field(None, description="Текст с переменными, как у текстового блока")
    value: str | None = Field(None, description="Готовое значение")
    capital: bool = Field(False, description="С заглавной буквы: «Сентябрь»")
    empty: Literal["stop", "blank", "keep"] = Field(
        "stop",
        description="Если значения нет: stop — остановить сборку, blank — оставить пустой, keep — оставить метку",
    )
    color: MarkerColor | None = Field(
        None, description="Цвет значения по знаку: sign — зелёный и красный, «1F4E79» — один цвет"
    )

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, v: Any) -> Any:
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("period."):
                name = s.removeprefix("period.")
                if name[:1].isupper():  # period.Month — с заглавной буквы
                    return {"period": name[:1].lower() + name[1:], "capital": True}
                return {"period": name}
            return {"metric": s}
        if isinstance(v, int | float):
            return {"value": str(v)}
        return v

    @model_validator(mode="after")
    def _check(self) -> MarkerBinding:
        given = [k for k in ("metric", "period", "text", "value") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("у привязки метки должен быть ровно один источник: metric, period, text или value")
        if self.period is not None and self.period not in PERIOD_VARS:
            raise ValueError(f"нет переменной периода «{self.period}» (есть: {', '.join(PERIOD_VARS)})")
        return self

    def describe(self) -> str:
        if self.metric:
            return f"показатель {self.metric}"
        if self.period:
            return f"period.{self.period}"
        if self.text is not None:
            return "текст"
        return "значение"


class MarkersParams(BaseModel):
    """Привязки меток слайда (``bindings``) и всей презентации (``common``). ``render`` кладёт
    в ``common`` только привязки меток, которые есть на этом слайде: тогда слайд зависит
    только от своих показателей."""

    model_config = ConfigDict(extra="forbid")

    bindings: dict[str, MarkerBinding] = Field(default_factory=dict)
    common: dict[str, MarkerBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _keys(self) -> MarkersParams:
        self.bindings = {_clean_key(k): v for k, v in self.bindings.items()}
        self.common = {_clean_key(k): v for k, v in self.common.items()}
        for k in self.common:
            if _KEY.match(k):
                raise ValueError(f"привязка «{k}» на всю презентацию задаётся по имени метки, без фигуры")
        return self


def _clean_key(key: str) -> str:
    k = str(key).strip()
    if k.startswith("{{") and "}}" in k:
        k = k[2:].replace("}}", "", 1)
    m = _KEY.match(k)
    if m:
        name = m.group("name").strip()
        return f"{name}@{m.group('shape')}" + (f"#{m.group('occ')}" if m.group("occ") else "")
    return k.strip()


def resolve(params: Any, name: str, shape_id: int, occurrence: int) -> tuple[MarkerBinding, str] | None:
    """Самая точная привязка вхождения и её ключ."""
    for key in (f"{name}@{shape_id}#{occurrence}", f"{name}@{shape_id}", name):
        if key in params.bindings:
            return params.bindings[key], key
    if name in params.common:
        return params.common[name], f"{name} (вся презентация)"
    return None


def binding_value(b: MarkerBinding, data: BlockData, ctx: BlockContext, after: str = "") -> str | None:
    """Значение привязки текстом; ``None`` — значения нет (показатель пуст или не посчитан)."""
    if b.value is not None:
        text: str | None = b.value
    elif b.period is not None:
        text = PERIOD_VARS[b.period](PeriodVars(ctx.period))
    elif b.text is not None:
        text = render_text(b.text, data.metrics, ctx)
    else:
        assert b.metric is not None
        text = b.format(data.metrics.get(b.metric), after)
    if text is None:
        return None
    text = one_line(text)
    if b.capital:
        text = text[:1].upper() + text[1:]
    return text


def _sign(v: Any) -> int | None:
    """Знак числа: 1, -1 или 0; ``None`` — пусто или не число."""
    if v is None or isinstance(v, bool | str):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return (x > 0) - (x < 0)


def value_color(b: MarkerBinding, value: str, metrics: dict[str, Any]) -> str | None:
    """Цвет значения по знаку; ``None`` — как в шаблоне. Знак — по показателю ``by`` (без него —
    по показателю привязки), а если показателя нет или он пуст — по тексту значения: «+» — плюс,
    «-» или «−» — минус, иначе ноль. Пустой показатель ``by`` не подменяется показателем привязки:
    иначе выручка с ``by: revenue_change`` без прошлого периода окрасилась бы как рост."""
    c = b.color
    if c is None:
        return None
    metric = c.by or b.metric
    sign = None if metric is None else _sign(metrics.get(metric))
    if sign is None:
        sign = 1 if "+" in value else -1 if "-" in value or "\u2212" in value else 0
    return c.pick(sign)


# --- замена в XML ------------------------------------------------------------------------


def _lang(rpr: etree._Element, value: str) -> None:
    if _CYRILLIC.search(value):
        rpr.set("lang", "ru-RU")
    elif _LATIN.search(value):
        rpr.set("lang", "en-US")
    for attr in ("err", "dirty"):
        if attr in rpr.attrib:
            del rpr.attrib[attr]


def _run_props(run: etree._Element) -> etree._Element:
    rpr = run.find(f"{A}rPr")
    if rpr is None:
        rpr = etree.SubElement(run, f"{A}rPr")
        run.insert(0, rpr)
    return rpr


def _set_text(run: etree._Element, text: str) -> None:
    t = run.find(f"{A}t")
    if t is None:
        t = etree.SubElement(run, f"{A}t")
    t.text = text


def _highlight(run: etree._Element) -> None:
    rpr = _run_props(run)
    for old in rpr.findall(f"{A}highlight"):
        rpr.remove(old)
    hl = etree.Element(f"{A}highlight")
    etree.SubElement(hl, f"{A}srgbClr", val=HIGHLIGHT)
    # a:highlight идёт после ln, заливки, эффектов и до uLnTx/latin/ea/cs/sym/hlinkClick
    anchor = next(
        (
            rpr.find(f"{A}{t}")
            for t in ("uLnTx", "uLn", "uFillTx", "uFill", "latin", "ea", "cs", "sym", "hlinkClick")
            if rpr.find(f"{A}{t}") is not None
        ),
        None,
    )
    if anchor is not None:
        anchor.addprevious(hl)
    else:
        rpr.append(hl)


def _color(rpr: etree._Element, rgb: str) -> None:
    for tag in ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"):
        for old in rpr.findall(f"{A}{tag}"):
            rpr.remove(old)
    fill = etree.Element(f"{A}solidFill")
    etree.SubElement(fill, f"{A}srgbClr", val=rgb)
    # заливка идёт сразу после a:ln, раньше эффектов, highlight, подчёркивания, шрифтов и ссылок
    ln = rpr.find(f"{A}ln")
    if ln is not None:
        ln.addnext(fill)
    else:
        rpr.insert(0, fill)


def replace_match(m: MarkerMatch, value: str | None, color: str | None = None) -> None:
    """Заменить вхождение метки значением (``None`` — подсветить метку и оставить как есть);
    ``color`` — цвет значения, RGB (``None`` — как в шаблоне).

    Текст перед меткой остаётся в своём прогоне, значение получает новый прогон с оформлением
    прогона, где метка начинается, текст после метки — прогон с оформлением прогона, где она
    кончается. Прогоны внутри метки удаляются. Текст берётся из XML заново: метки абзаца
    заменяются справа налево, и прогон левее мог уже укоротиться."""
    _, segs = paragraph_segments(m.p)
    covered = [s for s in segs if s.start < m.end and s.start + len(s.text) > m.start]
    if not covered or covered[0].kind != "r":
        return
    first, last = covered[0], covered[-1]
    if value is None:
        for s in covered:
            if s.kind == "r":
                _highlight(s.el)
        return
    before = first.text[: m.start - first.start]
    after = last.text[m.end - last.start :]
    new = copy.deepcopy(first.el)
    _set_text(new, value)
    _lang(_run_props(new), value)
    if color is not None:
        _color(_run_props(new), color)
    tail = None
    if after:
        tail = copy.deepcopy(last.el)
        _set_text(tail, after)
    anchor = first.el
    for s in covered[1:]:
        s.el.getparent().remove(s.el)
    if before:
        _set_text(first.el, before)
        anchor.addnext(new)
    else:
        anchor.addprevious(new)
        anchor.getparent().remove(anchor)
    if tail is not None:
        new.addnext(tail)


def _shape_tree(slide: Any) -> etree._Element:
    return slide._element.find(f"{P}cSld/{P}spTree")


# --- блок ---------------------------------------------------------------------------------


class MarkersBlock(BlockPlugin):
    name = "markers"
    title = "Метки слайда-образца"
    Params = MarkersParams
    target_kind = BlockTargetKind.MARKERS

    def data_needs(self, params: Any) -> DataNeeds:
        metrics: set[str] = set()
        for b in [*params.bindings.values(), *params.common.values()]:
            if b.metric:
                metrics.add(b.metric)
            elif b.text is not None:
                metrics |= referenced_metrics(b.text)
            if b.color is not None and b.color.by:
                metrics.add(b.color.by)
        return DataNeeds(metrics=metrics)

    def check(self, params: Any, example: TemplateSlideInfo | None, shape_id: int | None) -> list[str]:
        """Непривязанные и незаменяемые метки, привязки к меткам, которых нет на слайде."""
        if example is None:
            return ["метки заполняются только на слайде-образце"]
        problems: list[str] = []
        unbound: dict[str, list[str]] = {}
        for m in example.markers:
            if not m.replaceable:
                problems.append(f"{{{{{m.name}}}}} в «{m.shape_name}» нельзя заменить: {m.reason}")
                continue
            if resolve(params, m.name, m.shape_id, m.occurrence) is None:
                unbound.setdefault(m.name, []).append(f"«{m.shape_name}» (id {m.shape_id})")
        for name, where in unbound.items():
            problems.append(f"метка {{{{{name}}}}} не привязана: {', '.join(dict.fromkeys(where))}")
        keys = {(m.name, m.shape_id, m.occurrence) for m in example.markers}
        names = {m.name for m in example.markers}
        for key in params.bindings:
            km = _KEY.match(key)
            if km is None:
                ok = key in names
            else:
                name, shape = km.group("name"), int(km.group("shape"))
                occ = int(km.group("occ")) if km.group("occ") else None
                ok = any(n == name and s == shape and (occ is None or o == occ) for n, s, o in keys)
            if not ok:
                problems.append(f"на слайде нет метки для привязки «{key}»")
        return problems

    def _values(
        self, params: Any, example: TemplateSlideInfo, data: BlockData, ctx: BlockContext
    ) -> tuple[dict[tuple[int, str, int], tuple[str | None, MarkerBinding | None]], list[str]]:
        out: dict[tuple[int, str, int], tuple[str | None, MarkerBinding | None]] = {}
        problems: list[str] = []
        for m in example.markers:
            found = resolve(params, m.name, m.shape_id, m.occurrence)
            where = f"{{{{{m.name}}}}} в «{m.shape_name}»"
            if not m.replaceable:
                problems.append(f"{where} нельзя заменить: {m.reason}")
                out[(m.shape_id, m.name, m.occurrence)] = (None, None)
                continue
            if found is None:
                problems.append(f"{where}: метка не привязана")
                out[(m.shape_id, m.name, m.occurrence)] = (None, None)
                continue
            b, _ = found
            try:
                value = binding_value(b, data, ctx, m.text_after)
            except ValueError as e:
                problems.append(f"{where}: {e}")
                value = None
                b = b.model_copy(update={"empty": "stop"})
            if value is None and b.empty == "stop" and not any(where in p for p in problems):
                problems.append(f"{where}: нет значения ({b.describe()})")
            out[(m.shape_id, m.name, m.occurrence)] = (value, b)
        return out, problems

    def render(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> None:
        example = target.example
        if example is None:
            raise BlockError(["метки заполняются только на слайде-образце"])
        values, problems = self._values(params, example, data, ctx)
        if problems and not ctx.preview:
            raise BlockError(problems)
        for sh in iter_shapes(_shape_tree(target.slide)):
            matches = list(iter_markers(sh.el))
            for m in reversed(matches):
                value, b = values.get((sh.id, m.name, m.occurrence), (None, None))
                if m.reason is not None:
                    continue
                if value is None:
                    if b is not None and b.empty == "blank":
                        replace_match(m, "")
                        continue
                    if b is not None and b.empty == "keep":
                        ctx.keep_marker(sh.id, m.name)
                        continue
                    if ctx.preview:
                        replace_match(m, None)  # подсветить: в превью метка остаётся видна
                        ctx.keep_marker(sh.id, m.name)
                    continue
                replace_match(m, value, value_color(b, value, data.metrics) if b is not None else None)
        for p in problems:
            ctx.warn(p)

    def preview(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> PreviewSpec | None:
        example = target.example
        if example is None:
            return None
        values, problems = self._values(params, example, data, ctx)
        shown = {}
        for m in example.markers:
            shown[_label(m)] = values.get((m.shape_id, m.name, m.occurrence), (None, None))[0]
        return PreviewSpec(kind=PreviewKind.VALUES, values=shown, warnings=problems)


def _label(m: MarkerInfo) -> str:
    return f"{{{{{m.name}}}}} @{m.shape_id}#{m.occurrence}"
