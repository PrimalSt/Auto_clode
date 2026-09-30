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


@dataclass
class EngineResult:
    """Итог движка: небольшие агрегированные таблицы и числа — то, что уходит в render."""

    period: Period
    datasets: dict[str, pa.Table] = field(default_factory=dict)
    metrics: dict[str, float | int | None] = field(default_factory=dict)
    nodes: list[NodeStatus] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def failed_nodes(self) -> dict[str, NodeStatus]:
        return {n.id: n for n in self.nodes if n.state != "ok"}


class RenderResult(BaseModel):
    output_path: str | None = None
    slides: int = 0
    nodes: list[NodeStatus] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


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
    seconds: float = 0.0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.WARNING]
