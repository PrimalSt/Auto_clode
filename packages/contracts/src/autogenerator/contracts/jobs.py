"""Задания для исполнителя. Сейчас их ставит CLI через ``api``; позже — сервер через очередь
(ARCHITECTURE.md, раздел 6.6). Поэтому задание — данные: пути, а не открытые объекты."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .periods import Period
from .scenario import ScenarioSpec
from .sources import SourceSpec


class RunRequest(BaseModel):
    """Задание ``run``: собрать отчёт по сценарию."""

    scenario: ScenarioSpec
    sources: list[SourceSpec]
    inputs: dict[str, list[str]] = Field(description="id входа → файлы выгрузок в порядке загрузки (от старых к новым)")
    theme: str = Field(description="Путь к шаблону .pptx или .potx")
    period: Period | None = Field(None, description="Отчётный период; пусто — по основному входу")
    output: str | None = Field(None, description="Путь к .pptx; пусто — имя по сценарию в output_dir")
    output_dir: str | None = None
    workdir: str | None = Field(None, description="Рабочая папка; пусто — временная, удаляется после запуска")
    accept_cast_errors: bool = Field(False, description="Принять загрузки с ошибками приведения типов")
