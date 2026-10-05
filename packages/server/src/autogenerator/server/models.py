"""Тела запросов и ответов API, которых нет в контрактах."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from autogenerator.contracts import (
    CoverageReport,
    ExecutorInfo,
    Issue,
    OverlapPolicy,
    Period,
    PeriodFrom,
    PeriodUnit,
    PluginManifest,
    ReadOptions,
    ReconcileResult,
    RunResult,
    ScenarioRecord,
    SchemaSnapshot,
    SourceSpec,
    ThemeRecord,
    UploadRecord,
    UploadStatus,
)

# --- источники и загрузки ------------------------------------------------------------


class SourceDraftIn(BaseModel):
    """Черновик источника по образцу выгрузки (задание ``draft_source``)."""

    path: str = Field(description="Путь к файлу выгрузки на этом компьютере")
    id: str
    name: str | None = None
    period_column: str | None = None
    period_type: PeriodUnit | None = None
    period_from: PeriodFrom | None = Field(None, description="upload — выгрузка-срез: период задаётся при загрузке")
    options: ReadOptions | None = None
    format: str | None = None


class SourceDraftOut(BaseModel):
    source: SourceSpec
    snapshot: SchemaSnapshot


class SourceIn(BaseModel):
    spec: SourceSpec
    comment: str = ""
    force: bool = Field(False, description="Убрать столбцы, которые есть в загрузках (их данные пропадут из истории)")


class SourcesImportIn(BaseModel):
    path: str = Field(description="Файл YAML с источниками")
    comment: str = ""
    force: bool = False


class UploadIn(BaseModel):
    """Загрузка выгрузки (задание ``upload``). Если загрузка остановилась на сопоставлении
    (``schema_review``) или пересечении периодов (``overlap_choice``), окно показывает выбор и
    отправляет загрузку снова с ``mapping``/``declined`` или ``overlap_policy``."""

    paths: list[str] = Field(min_length=1, description="Файл выгрузки или её части по порядку — одна загрузка")
    options: ReadOptions | None = None
    period: str | Period | None = Field(None, description="Период среза: 2026-03, 2026-Q1, диапазон")
    overlap_policy: OverlapPolicy | None = None
    accept_cast_errors: bool = False
    mapping: dict[str, str] = Field(default_factory=dict, description="Подтверждённые пары «название в файле → id»")
    declined: list[str] = Field(default_factory=list, description="id столбцов, которые оставить пустыми")
    accept_mapping: bool = Field(False, description="Принять предложенные пары («Принять все»)")
    force: bool = Field(False, description="Загрузить файл, который уже загружен")
    profile: bool = True


class UploadOut(BaseModel):
    """Итог задания ``upload``."""

    record: UploadRecord
    issues: list[Issue] = Field(default_factory=list)
    remembered: dict[str, str] = Field(default_factory=dict, description="Запомненные названия «в файле → id»")
    reconcile: ReconcileResult


class UploadPatch(BaseModel):
    """Изменить загрузку: принять, исключить из истории, вернуть (``status``), период, правило
    пересечения (``null`` — правило источника). Меняются только переданные поля."""

    status: UploadStatus | None = None
    period: str | Period | None = None
    overlap_policy: OverlapPolicy | None = None


class ColumnUsageOut(BaseModel):
    """Какие столбцы источника нужны сохранённым сценариям."""

    required: list[str] | None = Field(description="id нужных столбцов; null — источник не используют сценарии")
    dependents: dict[str, list[str]] = Field(default_factory=dict, description="id столбца → кто его использует")


class HistoryOut(BaseModel):
    uploads: list[UploadRecord]
    coverage: CoverageReport | None = Field(None, description="Шкала покрытия; пусто, если активных загрузок нет")
    disk_usage: int = Field(description="Сколько места занимают загрузки источника, байт")


# --- сценарии, шаблоны, запуски ----------------------------------------------------


class ScenarioIn(BaseModel):
    """Сценарий из окна: текст YAML (редактор кода, сохраняется как есть) или JSON той же схемы."""

    text: str | None = None
    spec: dict[str, Any] | None = None
    id: str | None = Field(None, description="id нового сценария; по умолчанию — по названию")
    theme: str | None = Field(None, description="id шаблона из папки данных (иначе — поле theme сценария)")
    comment: str = ""


class SavedScenarioOut(BaseModel):
    """Сохранённая версия и итог проверки: сценарий с ошибками сохраняется как черновик."""

    record: ScenarioRecord
    issues: list[Issue] = Field(default_factory=list)


class ScenarioCopyIn(BaseModel):
    id: str
    name: str | None = None


class RunIn(BaseModel):
    period: str | Period | None = Field(None, description="Отчётный период; по умолчанию — последний основного входа")
    version: int | None = Field(None, description="Версия сценария; по умолчанию — текущая")
    output: str | None = Field(None, description="Папка или файл для копии отчёта")
    accept_cast_errors: bool = False


class RerunIn(BaseModel):
    output: str | None = None


class ThemeIn(BaseModel):
    path: str = Field(description="Файл .pptx или .potx на этом компьютере")
    id: str | None = Field(None, description="id шаблона: существующий — его новая версия")
    name: str | None = None
    comment: str = ""


class ThemeImportOut(BaseModel):
    """Итог импорта шаблона или подтверждения ролей."""

    record: ThemeRecord
    skipped: bool = Field(False, description="Новой версии нет: файл уже загружен (matched) или роли не изменились")
    matched: int | None = None
    scenarios: list[str] = Field(default_factory=list, description="Сценарии, перешедшие на новую версию")
    lost: dict[str, list[str]] = Field(
        default_factory=dict, description="Сценарий → что не сходится на новой версии (остался на прежней)"
    )


class ReimportIn(BaseModel):
    path: str
    comment: str = ""


class RolesIn(BaseModel):
    roles: dict[str, str | None] = Field(description="Роль → ключ макета; null — снять подтверждение")
    comment: str = ""


class ExportIn(BaseModel):
    out: str = Field(description="Папка или файл .pptx/.potx")
    version: int | None = None


# --- превью ---------------------------------------------------------------------


class DraftIn(BaseModel):
    text: str | None = None
    spec: dict[str, Any] | None = None


class NodePreviewIn(DraftIn):
    target: str = Field(description="Вход после шага (sales/dedupe), набор (dataset:…) или показатель (metric:…)")
    period: str | Period | None = None
    rows: int = Field(20, ge=1, le=1000)
    sample: int | None = None


class SlidePreviewIn(DraftIn):
    slide: int = Field(ge=1, description="Номер слайда среди включённых, с единицы")
    period: str | Period | None = None
    image: bool = True


class SlidePreviewOut(BaseModel):
    id: str
    result: RunResult
    files: dict[str, str] = Field(description="Файлы пробной сборки: pptx, png — пути API")


# --- система и модули --------------------------------------------------------------


class SystemOut(BaseModel):
    app: str = "autogenerator"
    version: str
    pid: int
    home: str
    started_at: datetime
    disk_free: int
    warnings: list[str]
    jobs_active: int
    executors: list[ExecutorInfo]
    dev: bool = False
    """Режим разработчика (сервер запущен с ``--dev``)."""
    dev_source: str | None = None
    """Копия исходников, из которой запущен сервер (режим разработчика), иначе None."""


class BackupIn(BaseModel):
    label: str = "manual"


class BackupOut(BaseModel):
    path: str
    name: str
    size: int
    created: datetime


class RestoreIn(BaseModel):
    path: str = Field(description="Файл резервной копии или его имя в папке backups")


class ModulesOut(BaseModel):
    executors: list[ExecutorInfo]
    plugins: PluginManifest | None = None
    error: dict[str, Any] | None = Field(None, description="Почему манифест модулей не получен")


class ModulesCheckIn(BaseModel):
    modules: list[str] | None = None
    """Какие модули проверить (папки ``packages/*``, ``_`` и ``-`` взаимозаменяемы); по умолчанию —
    изменённые после запуска сервера или прошлого применения."""
    apply: bool = True
    """Если тесты прошли — перезапустить исполнители с новым кодом. Новый код применяется, только
    если среди проверенных все изменённые модули (исполнители загружают весь код с диска)."""
