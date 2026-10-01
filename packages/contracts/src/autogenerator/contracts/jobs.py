"""Задания для исполнителя. Сейчас их ставит CLI через ``api``; позже — сервер через очередь
(ARCHITECTURE.md, раздел 6.6). Поэтому задание — данные: пути, а не открытые объекты."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .history import HistoryManifest
from .periods import Period
from .results import Issue
from .scenario import ScenarioSpec
from .snapshot import ColumnProfile, ReconcileResult, SchemaSnapshot, UploadResult
from .sources import OverlapPolicy, ReadOptions, SourceSpec


class RunRequest(BaseModel):
    """Задание ``run``: собрать отчёт по сценарию."""

    scenario: ScenarioSpec
    sources: list[SourceSpec]
    inputs: dict[str, list[str]] = Field(
        default_factory=dict, description="id входа → файлы выгрузок в порядке загрузки (от старых к новым)"
    )
    histories: dict[str, HistoryManifest] = Field(
        default_factory=dict,
        description="id входа → манифест истории из папки данных; такие входы не читают файлы заново",
    )
    theme: str = Field(description="Путь к шаблону .pptx или .potx")
    period: Period | None = Field(None, description="Отчётный период; пусто — по основному входу")
    output: str | None = Field(None, description="Путь к .pptx; пусто — имя по сценарию в output_dir")
    output_dir: str | None = None
    workdir: str | None = Field(None, description="Рабочая папка; пусто — временная, удаляется после запуска")
    accept_cast_errors: bool = Field(False, description="Принять загрузки с ошибками приведения типов")


class IngestRequest(BaseModel):
    """Задание ``ingest``: прочитать файл и записать загрузку источника (раздел 6.1).

    Исполнитель не открывает метаданные: всё нужное приходит в задании, а результат
    фиксирует тот, кто его поставил (CLI сейчас, сервер потом).
    """

    source: SourceSpec
    path: str
    upload_id: str
    upload_seq: int
    out_dir: str = Field(description="Папка загрузки; пишется атомарно, после сбоя не остаётся половины")
    options: ReadOptions | None = Field(None, description="Параметры чтения поверх настроек источника")
    required: list[str] = Field(default_factory=list, description="id столбцов, которые нужны сценариям")
    period: Period | None = Field(None, description="Период загрузки, заданный пользователем")
    history: HistoryManifest | None = Field(
        None, description="Текущая история источника: для предупреждений о пересечении периодов"
    )
    overlap_policy: OverlapPolicy | None = Field(None, description="Выбор для источника с правилом «ask»")
    profile: bool = Field(True, description="Посчитать точный профиль столбцов по записанной загрузке")


class IngestResult(BaseModel):
    """Итог задания ``ingest``."""

    snapshot: SchemaSnapshot
    reconcile: ReconcileResult
    upload: UploadResult
    period: Period = Field(description="Объявленный период загрузки")
    period_from_data: Period = Field(description="Период по датам в данных (до правки пользователем)")
    rows_outside_period: int = Field(0, description="Строки с датой вне объявленного периода")
    overlaps: list[str] = Field(default_factory=list, description="id активных загрузок, чей период пересекается")
    overlap_policy: OverlapPolicy | None = Field(None, description="Правило для этой загрузки, если выбрано")
    needs_overlap_choice: bool = Field(
        False, description="У источника правило «ask», период пересекается с прежними, а выбора нет"
    )
    profile: dict[str, ColumnProfile] = Field(default_factory=dict, description="Точный профиль: id столбца → профиль")
    issues: list[Issue] = Field(default_factory=list)
