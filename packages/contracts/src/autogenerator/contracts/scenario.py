"""Сценарий — декларативная спецификация отчёта: входы, шаги обработки, наборы данных,
показатели и слайды (ARCHITECTURE.md, раздел 8).

Параметры шагов и блоков здесь не описаны: их модель задаёт плагин (``Params``), а сценарий
хранит их как есть. Проверку параметров делает хозяин плагина (engine или render).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .periods import PeriodUnit

SPEC_VERSION = 1


class _Extensible(BaseModel):
    """Узел, у которого кроме общих полей есть параметры плагина."""

    model_config = ConfigDict(extra="allow")

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class StepSpec(_Extensible):
    """Шаг обработки входа. Всё, кроме общих полей, — параметры плагина шага."""

    id: str
    type: str
    type_version: int = 1
    enabled: bool = True


class InputSpec(BaseModel):
    """Вход сценария: история источника и её обработка."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    main: bool = Field(False, description="По основному входу определяется отчётный период")
    pipeline: list[StepSpec] = Field(default_factory=list)


class WindowSpec(_Extensible):
    """Окно данных: какой отрезок истории берётся относительно отчётного периода."""

    type: str = "report_period"

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, value: Any) -> Any:
        # Короткая запись: "quarter_to_date", "last_n(6)", "range(2026-01-01, 2026-03-31)".
        if isinstance(value, str):
            m = re.fullmatch(r"\s*(\w+)\s*(?:\((.*)\))?\s*", value)
            if not m:
                raise ValueError(f"Не понял окно «{value}»")
            name, args = m.group(1), m.group(2)
            data: dict[str, Any] = {"type": name}
            if args:
                parts = [a.strip() for a in args.split(",") if a.strip()]
                if name == "last_n" and len(parts) == 1:
                    data["n"] = int(parts[0])
                elif name == "range" and len(parts) == 2:
                    data["start"], data["end"] = parts
                else:
                    raise ValueError(f"Не понял параметры окна «{value}»")
            return data
        return value

    def describe(self) -> str:
        if not self.params:
            return self.type
        args = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.type}({args})"


class GroupBySpec(BaseModel):
    """Группировка по столбцу; для столбца дат можно указать единицу («по месяцам»)."""

    model_config = ConfigDict(extra="forbid")

    column: str
    bucket: PeriodUnit | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, value: Any) -> Any:
        return {"column": value} if isinstance(value, str) else value


class AggregateSpec(BaseModel):
    """Агрегат набора данных: ``fn`` по столбцу ``column``, результат — столбец ``as``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fn: str
    column: str | None = None
    as_: str | None = Field(None, alias="as")

    @property
    def output_name(self) -> str:
        if self.as_:
            return self.as_
        return f"{self.fn}_{self.column}" if self.column else self.fn


class SortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    desc: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, value: Any) -> Any:
        # "revenue" — по возрастанию, "-revenue" — по убыванию.
        if isinstance(value, str):
            v = value.strip()
            return {"column": v.lstrip("-"), "desc": v.startswith("-")}
        return value


COMPARE_SUFFIXES = {"previous_period": "prev", "same_period_last_year": "ly"}


class CompareSpec(BaseModel):
    """Сравнение с другим периодом (F-305): то же окно, посчитанное за предыдущий отчётный
    период или за тот же период год назад. Короткая запись — имя: ``previous_period``."""

    model_config = ConfigDict(extra="forbid")

    window: Literal["previous_period", "same_period_last_year"]
    suffix: str | None = Field(None, description="Окончание новых столбцов и показателей: prev, ly, …")

    @model_validator(mode="before")
    @classmethod
    def _from_text(cls, value: Any) -> Any:
        return {"window": value} if isinstance(value, str) else value

    @property
    def tag(self) -> str:
        return self.suffix or COMPARE_SUFFIXES[self.window]


class DeriveSpec(BaseModel):
    """Расчёт над готовым набором (F-306): доля от итога, накопительный итог, ранг или
    формула из столбцов набора."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fn: Literal["share", "cumsum", "rank", "formula"] | None = None
    column: str | None = None
    expr: str | None = Field(None, description="Формула на SQL из столбцов набора: fact / plan")
    as_: str | None = Field(None, alias="as")
    desc: bool = Field(True, description="Ранг: 1 — у самого большого значения")

    @model_validator(mode="after")
    def _check(self) -> DeriveSpec:
        if self.expr is not None:
            if self.fn not in (None, "formula"):
                raise ValueError("у расчёта с expr не задаётся fn")
            self.fn = "formula"
            if not self.as_:
                raise ValueError(f"формуле «{self.expr}» нужно имя столбца (as)")
        elif self.fn is None or self.fn == "formula":
            raise ValueError("нужен fn (share, cumsum, rank) или expr")
        elif not self.column:
            raise ValueError(f"расчёту {self.fn} нужен столбец")
        return self

    @property
    def output_name(self) -> str:
        if self.as_:
            return self.as_
        return f"{self.column}_{self.fn}"


class DatasetSpec(BaseModel):
    """Набор данных: вход → окно → фильтр → группировка → агрегаты → сравнение периодов →
    расчёты → сортировка → топ-N (с «Прочими») или сводная таблица.

    Без ``aggregate`` набор — это строки входа (столбцы ``columns``), с сортировкой и топ-N.
    ``type: sql`` — набор задан запросом ``query`` к входам и другим наборам по их ``id``;
    ``type: python`` — функцией ``build(tables, ctx)`` в ``code`` (F-308). Окно у таких
    наборов применяется к каждому входу до запроса или кода.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None = None
    type: Literal["table", "sql", "python"] = "table"
    input: str | None = None
    window: WindowSpec = Field(default_factory=WindowSpec)
    where: str | None = Field(None, description="Условие на SQL (диалект DuckDB)")
    group_by: list[GroupBySpec] = Field(default_factory=list)
    aggregate: list[AggregateSpec] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    compare: list[CompareSpec] = Field(default_factory=list)
    derive: list[DeriveSpec] = Field(default_factory=list)
    sort: list[SortSpec] | None = None
    top: int | None = Field(None, ge=1)
    others: str | None = Field(None, description="Строка для всего, что не вошло в топ-N, например «Прочие»")
    pivot: str | None = Field(None, description="Столбец группировки, значения которого становятся столбцами")
    query: str | None = Field(None, description="Запрос SQL (type: sql)")
    code: str | None = Field(None, description="Код на Python с функцией build(tables, ctx) (type: python)")
    inputs: list[str] = Field(default_factory=list, description="Входы и наборы, которые получает код (type: python)")
    frame: Literal["pandas", "polars"] = "pandas"

    @model_validator(mode="before")
    @classmethod
    def _infer_type(cls, value: Any) -> Any:
        if isinstance(value, dict) and "type" not in value:
            if "query" in value:
                return {**value, "type": "sql"}
            if "code" in value:
                return {**value, "type": "python"}
        return value

    @model_validator(mode="after")
    def _check(self) -> DatasetSpec:
        name = f"Набор «{self.id}»"
        if self.type == "table":
            if not self.input:
                raise ValueError(f"{name}: нужен вход (input)")
            if self.query or self.code or self.inputs:
                raise ValueError(f"{name}: query, code и inputs задаются только у наборов type: sql и python")
            if self.aggregate and self.columns:
                raise ValueError(f"{name}: columns задаются только без aggregate")
            if not self.aggregate and self.group_by:
                raise ValueError(f"{name}: у группировки должен быть хотя бы один агрегат")
            if not self.aggregate and (self.compare or self.pivot or self.others):
                raise ValueError(f"{name}: compare, pivot и others работают только с aggregate")
            if self.others is not None:
                if not self.top:
                    raise ValueError(f"{name}: others задаётся вместе с top")
                if len(self.group_by) != 1:
                    raise ValueError(f"{name}: «{self.others}» собирается только при группировке по одному столбцу")
            if self.pivot is not None:
                if self.pivot not in [g.column for g in self.group_by]:
                    raise ValueError(f"{name}: pivot «{self.pivot}» должен быть столбцом группировки")
                if self.derive or self.compare or self.others:
                    raise ValueError(f"{name}: сводная таблица (pivot) не сочетается с derive, compare и others")
        else:
            extra = [
                f for f in ("input", "where", "columns", "pivot", "others") if getattr(self, f) not in (None, [])
            ] + [f for f in ("group_by", "aggregate", "compare", "derive") if getattr(self, f)]
            if extra:
                raise ValueError(f"{name}: у набора type: {self.type} не задаются {', '.join(extra)}")
            if self.type == "sql" and not self.query:
                raise ValueError(f"{name}: нужен запрос (query)")
            if self.type == "python":
                if not self.code:
                    raise ValueError(f"{name}: нужен код (code) с функцией build(tables, ctx)")
                if not self.inputs:
                    raise ValueError(f"{name}: перечислите входы и наборы, которые получает код (inputs)")
        return self


class MetricSpec(BaseModel):
    """Показатель — одно число (F-304): агрегат входа в своём окне, агрегат набора данных,
    формула из других показателей, запрос SQL или код на Python (F-308).

    ``compare`` добавляет показатели сравнения (F-305): для ``revenue`` с
    ``compare: [previous_period]`` — ``revenue_prev``, ``revenue_prev_change`` и
    ``revenue_prev_change_pct``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None = None
    input: str | None = None
    dataset: str | None = None
    window: WindowSpec = Field(default_factory=WindowSpec)
    fn: str | None = None
    column: str | None = None
    where: str | None = None
    formula: str | None = Field(None, description="Формула из id других показателей, например revenue / plan")
    query: str | None = Field(None, description="Запрос SQL, который возвращает одно число")
    code: str | None = Field(None, description="Код на Python с функцией value(tables, ctx)")
    inputs: list[str] = Field(default_factory=list, description="Входы и наборы, которые получает код")
    frame: Literal["pandas", "polars"] = "pandas"
    compare: list[CompareSpec] = Field(default_factory=list)

    @property
    def kind(self) -> Literal["input", "dataset", "formula", "sql", "python"]:
        if self.formula is not None:
            return "formula"
        if self.query is not None:
            return "sql"
        if self.code is not None:
            return "python"
        return "dataset" if self.dataset is not None else "input"

    @model_validator(mode="after")
    def _check(self) -> MetricSpec:
        name = f"Показатель «{self.id}»"
        given = [
            k
            for k, v in (
                ("formula", self.formula),
                ("input", self.input),
                ("dataset", self.dataset),
                ("query", self.query),
                ("code", self.code),
            )
            if v is not None
        ]
        if not given:
            raise ValueError(f"{name}: нужна formula или пара input + fn (или dataset + fn, query, code)")
        if len(given) > 1:
            raise ValueError(f"{name}: {' и '.join(given)} вместе не задаются")
        kind = self.kind
        if kind in ("input", "dataset") and self.fn is None:
            raise ValueError(f"{name}: нужна формула или пара {kind} + fn")
        if kind in ("formula", "sql", "python") and (self.fn or self.column or self.where):
            raise ValueError(f"{name}: fn, column и where задаются только вместе с input или dataset")
        # окно по умолчанию не мешает: так сценарий переживает сохранение целиком (model_dump)
        if kind in ("formula", "dataset") and "window" in self.model_fields_set and self.window != WindowSpec():
            raise ValueError(f"{name}: окно задаётся у входа, а не у {'формулы' if kind == 'formula' else 'набора'}")
        if kind == "python" and not self.inputs:
            raise ValueError(f"{name}: перечислите входы и наборы, которые получает код (inputs)")
        if kind != "python" and self.inputs:
            raise ValueError(f"{name}: inputs задаются только вместе с code")
        return self

    def compare_ids(self) -> list[str]:
        """Показатели, которые добавляет ``compare``."""
        out: list[str] = []
        for c in self.compare:
            base = f"{self.id}_{c.tag}"
            out += [base, f"{base}_change", f"{base}_change_pct"]
        return out


class ShapeRef(BaseModel):
    """Фигура слайда-образца: адресуется по id (``cNvPr id``), имя — только подпись."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="id фигуры на слайде шаблона")
    label: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_int(cls, value: Any) -> Any:
        return {"id": value} if isinstance(value, int) else value


class BlockSpec(_Extensible):
    """Блок слайда. Всё, кроме общих полей, — параметры плагина блока.

    На слайде из макета блок ставится в область ``slot``; на слайде-образце блок заполняет
    готовую фигуру шаблона ``shape`` (график — ``chart_fill``, таблицу — ``table_fill``)."""

    type: str
    type_version: int = 1
    id: str | None = None
    slot: str | None = Field(None, description="Область макета: title, body, left, right, …")
    shape: ShapeRef | None = Field(None, description="Фигура слайда-образца")


class ExampleRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="sldId слайда шаблона")
    label: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_int(cls, value: Any) -> Any:
        return {"id": value} if isinstance(value, int) else value


class SlideSpec(BaseModel):
    """Слайд отчёта: из макета (``layout`` — роль макета) или слайд-образец шаблона
    (``example`` — id слайда шаблона).

    ``markers`` — привязки меток слайда-образца: ``имя`` (все вхождения на слайде),
    ``имя@фигура`` (вхождения в одной фигуре) или ``имя@фигура#номер`` (одно вхождение).
    Привязки на уровне сценария (``ScenarioSpec.markers``) действуют на всю презентацию.
    ``keep`` — фигуры шаблона (графики, таблицы), которые намеренно остаются как в шаблоне.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    layout: str | None = None
    example: ExampleRef | None = None
    enabled: bool = True
    markers: dict[str, Any] = Field(default_factory=dict)
    blocks: list[BlockSpec] = Field(default_factory=list)
    keep: list[int] = Field(default_factory=list, description="id фигур, которые не заполняются")

    @model_validator(mode="after")
    def _check(self) -> SlideSpec:
        if (self.layout is None) == (self.example is None):
            raise ValueError("У слайда должен быть ровно один из layout или example")
        if self.layout is not None and (self.markers or self.keep):
            raise ValueError("markers и keep задаются только у слайда-образца (example)")
        return self


class ScenarioSettings(BaseModel):
    """Настройки сценария."""

    model_config = ConfigDict(extra="forbid")

    relative_to: Literal["report_period", "run_date"] = Field(
        "report_period",
        description="От чего отсчитываются относительные периоды фильтров (F-203): конец отчётного "
        "периода или дата запуска",
    )


RESERVED_TABLES = {"data"}
"""Имя ``data`` в шаге SQL — текущая таблица, поэтому вход или набор так называть нельзя."""


class ScenarioSpec(BaseModel):
    """Сценарий отчёта целиком."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = SPEC_VERSION
    name: str
    theme: str | None = Field(None, description="Оформление: id шаблона или путь к .pptx")
    output_name: str = Field("Отчёт_{period}", description="Имя файла; {period} — отчётный период")
    settings: ScenarioSettings = Field(default_factory=ScenarioSettings)
    inputs: list[InputSpec]
    datasets: list[DatasetSpec] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)
    markers: dict[str, Any] = Field(
        default_factory=dict, description="Привязки меток слайдов-образцов для всей презентации"
    )
    slides: list[SlideSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> ScenarioSpec:
        if not self.inputs:
            raise ValueError("В сценарии нет ни одного входа")
        mains = [i.id for i in self.inputs if i.main]
        if len(mains) > 1:
            raise ValueError(f"Основной вход может быть только один, отмечены: {', '.join(mains)}")
        # Входы и наборы — таблицы (в SQL на них ссылаются по id), поэтому их id не должны
        # совпадать. У показателей своё пространство имён: вход plan и показатель plan — можно.
        seen: dict[str, str] = {}
        for kind, items in (("вход", self.inputs), ("набор", self.datasets)):
            for it in items:
                if it.id in RESERVED_TABLES:
                    raise ValueError(f"{kind} не может называться «{it.id}»: так в SQL называется текущая таблица")
                if it.id in seen:
                    raise ValueError(f"id «{it.id}» повторяется: {seen[it.id]} и {kind}")
                seen[it.id] = kind
        metric_ids = [m.id for m in self.metrics]
        for m in self.metrics:
            metric_ids += m.compare_ids()
        dupes = sorted({m for m in metric_ids if metric_ids.count(m) > 1})
        if dupes:
            raise ValueError(
                f"id показателей повторяются: {', '.join(dupes)} (сравнение периодов добавляет показатели "
                "с окончаниями _prev, _ly, _change, _change_pct)"
            )
        return self

    @property
    def main_input(self) -> InputSpec:
        for i in self.inputs:
            if i.main:
                return i
        return self.inputs[0]

    def input(self, input_id: str) -> InputSpec:
        for i in self.inputs:
            if i.id == input_id:
                return i
        raise KeyError(input_id)
