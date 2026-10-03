"""Результаты этапов: статусы узлов, замечания, итог движка, сборки и запуска."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field

from .periods import Period


class IssueLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Issue(BaseModel):
    """Замечание для журнала запуска: предупреждение или ошибка с местом, где она возникла."""

    level: IssueLevel = IssueLevel.WARNING
    message: str
    node: str | None = Field(None, description="Узел графа: input:sales, dataset:by_month, slide:2, …")
    code: str | None = None

    def __str__(self) -> str:
        where = f"[{self.node}] " if self.node else ""
        return f"{where}{self.message}"


class NodeKind(StrEnum):
    INPUT = "input"
    DATASET = "dataset"
    METRIC = "metric"
    SLIDE = "slide"


class NodeState(StrEnum):
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


class NodeStatus(BaseModel):
    """Статус узла графа. ``SKIPPED`` — не считался, потому что упал узел, от которого он
    зависит (``blocked_by``)."""

    id: str
    kind: NodeKind
    state: NodeState
    message: str | None = None
    blocked_by: str | None = None
    rows_in: int | None = None
    rows_out: int | None = None
    seconds: float | None = None


class StepStat(BaseModel):
    """Шаг обработки входа в превью и журнале: сколько строк было и стало, сколько времени."""

    id: str
    type: str
    enabled: bool = True
    rows_before: int | None = None
    rows_after: int | None = None
    seconds: float | None = None
    error: str | None = None


class SampleInfo(BaseModel):
    """Выборка, на которой построено превью: строки с ``hash(ключ) % k == 0``."""

    k: int = Field(description="Берётся примерно каждая k-я строка (k-й ключ)")
    keys: dict[str, list[str]] = Field(
        default_factory=dict, description="Вход → столбцы ключа выборки (пусто — номер строки)"
    )
    inputs: list[str] = Field(default_factory=list, description="Входы, взятые выборкой; остальные — целиком")


class PreviewColumn(BaseModel):
    name: str
    dtype: str


class PreviewResult(BaseModel):
    """Превью узла сценария (ARCHITECTURE.md, раздел 6.4): первые строки, число строк до и
    после каждого шага, значение показателя. На больших данных — по выборке, числа строк
    тогда приблизительные (``approximate``) и пересчитаны на всю историю."""

    target: str = Field(description="Узел: input:sales, input:sales/step:dedupe, dataset:by_month, metric:revenue")
    period: Period | None = None
    columns: list[PreviewColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list, description="Первые строки результата")
    total_rows: int | None = None
    value: float | int | None = Field(None, description="Значение показателя")
    metrics: dict[str, float | int | None] = Field(
        default_factory=dict, description="Показатель и добавленные к нему сравнения периодов"
    )
    steps: list[StepStat] = Field(default_factory=list)
    sample: SampleInfo | None = None
    approximate: bool = False
    nodes: list[NodeStatus] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    seconds: float = 0.0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]


@dataclass
class EngineResult:
    """Итог движка: небольшие агрегированные таблицы и числа — то, что уходит в render."""

    period: Period
    datasets: dict[str, pa.Table] = field(default_factory=dict)
    metrics: dict[str, float | int | None] = field(default_factory=dict)
    nodes: list[NodeStatus] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    steps: dict[str, list[StepStat]] = field(default_factory=dict)

    def failed_nodes(self) -> dict[str, NodeStatus]:
        return {n.id: n for n in self.nodes if n.state != "ok"}


class RenderResult(BaseModel):
    output_path: str | None = None
    slides: int = 0
    nodes: list[NodeStatus] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class EnvironmentInfo(BaseModel):
    """Версии, от которых зависит результат запуска (F-608): приложение, Python, библиотеки
    вычислений и сборки, плагины и отпечаток окружения."""

    app_version: str
    python: str
    libraries: dict[str, str] = Field(default_factory=dict, description="Библиотека → версия")
    plugins: dict[str, str] = Field(default_factory=dict, description="«вид:имя» → версия пакета плагина")
    env_hash: str = Field("", description="sha256 списка установленных пакетов с версиями")


class RunResult(BaseModel):
    """Итог запуска сценария: что собрано, за какой период и что пошло не так."""

    ok: bool
    scenario: str
    period: Period | None = None
    output_path: str | None = None
    slides: int = 0
    workdir: str | None = None
    nodes: list[NodeStatus] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict, description="Загрузки по входам")
    from_home: list[str] = Field(default_factory=list, description="Входы, чья история взята из папки данных")
    image_path: str | None = Field(None, description="Картинка слайда (превью слайда)")
    image_note: str | None = Field(None, description="Пометка к картинке: «приблизительно», если рисовал не PowerPoint")
    environment: EnvironmentInfo | None = None
    seconds: float = 0.0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.WARNING]
