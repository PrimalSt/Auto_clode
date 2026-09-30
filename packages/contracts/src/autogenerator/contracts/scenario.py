"""Сценарий — декларативная спецификация отчёта: входы, шаги обработки, наборы данных,
показатели и слайды (ARCHITECTURE.md, раздел 8).

Параметры шагов и блоков здесь не описаны: их модель задаёт плагин (``Params``), а сценарий
хранит их как есть. Проверку параметров делает хозяин плагина (engine или render).
"""

from __future__ import annotations

import re
from typing import Any

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


class DatasetSpec(BaseModel):
    """Набор данных: вход → окно → фильтр → группировка → агрегаты → сортировка → топ-N.

    Без ``aggregate`` набор — это строки входа (столбцы ``columns``), с сортировкой и топ-N.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    input: str
    label: str | None = None
    window: WindowSpec = Field(default_factory=WindowSpec)
    where: str | None = Field(None, description="Условие на SQL (диалект DuckDB)")
    group_by: list[GroupBySpec] = Field(default_factory=list)
    aggregate: list[AggregateSpec] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    sort: list[SortSpec] | None = None
    top: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _check(self) -> DatasetSpec:
        if self.aggregate and self.columns:
            raise ValueError(f"Набор «{self.id}»: columns задаются только без aggregate")
        if not self.aggregate and self.group_by:
            raise ValueError(f"Набор «{self.id}»: у группировки должен быть хотя бы один агрегат")
        return self


class MetricSpec(BaseModel):
    """Показатель — одно число: агрегат в своём окне или формула из других показателей."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None = None
    input: str | None = None
    window: WindowSpec = Field(default_factory=WindowSpec)
    fn: str | None = None
    column: str | None = None
    where: str | None = None
    formula: str | None = Field(None, description="Формула из id других показателей, например revenue / plan")

    @model_validator(mode="after")
    def _check(self) -> MetricSpec:
        if self.formula is None and (self.input is None or self.fn is None):
            raise ValueError(f"Показатель «{self.id}»: нужна formula или пара input + fn")
        if self.formula is not None and self.input is not None:
            raise ValueError(f"Показатель «{self.id}»: formula и input вместе не задаются")
        return self


class BlockSpec(_Extensible):
    """Блок слайда. Всё, кроме общих полей, — параметры плагина блока."""

    type: str
    type_version: int = 1
    id: str | None = None
    slot: str | None = Field(None, description="Область макета: title, body, left, right, …")


class ExampleRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(description="sldId слайда шаблона")
    label: str | None = None


class SlideSpec(BaseModel):
    """Слайд отчёта: из макета (``layout`` — роль макета) или слайд-образец шаблона
    (``example``; появится на этапе M3)."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    layout: str | None = None
    example: ExampleRef | None = None
    enabled: bool = True
    markers: dict[str, Any] = Field(default_factory=dict)
    blocks: list[BlockSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> SlideSpec:
        if (self.layout is None) == (self.example is None):
            raise ValueError("У слайда должен быть ровно один из layout или example")
        return self


class ScenarioSpec(BaseModel):
    """Сценарий отчёта целиком."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = SPEC_VERSION
    name: str
    theme: str | None = Field(None, description="Оформление: id шаблона или путь к .pptx")
    output_name: str = Field("Отчёт_{period}", description="Имя файла; {period} — отчётный период")
    inputs: list[InputSpec]
    datasets: list[DatasetSpec] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)
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
                if it.id in seen:
                    raise ValueError(f"id «{it.id}» повторяется: {seen[it.id]} и {kind}")
                seen[it.id] = kind
        metric_ids = [m.id for m in self.metrics]
        dupes = sorted({m for m in metric_ids if metric_ids.count(m) > 1})
        if dupes:
            raise ValueError(f"id показателей повторяются: {', '.join(dupes)}")
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
