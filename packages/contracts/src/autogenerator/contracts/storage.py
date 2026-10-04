"""Записи и интерфейсы хранилищ (ARCHITECTURE.md, раздел 4.2, правило 7; раздел 9).

Метаданные (источники, загрузки, сценарии, шаблоны оформления, запуски и их версии) хранит
``MetadataStore``, файлы загрузок, шаблонов и отчётов — ``BlobStore``. Для одного
пользователя на компьютере это SQLite и папка на диске (пакет ``storage``); для сервера
реализации заменяются, а модули обработки не меняются: они видят только манифесты и URI.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .history import HistoryManifest
from .periods import Period
from .results import RunResult
from .scenario import ScenarioSpec
from .snapshot import CastIssue, ColumnProfile, SchemaSnapshot, UploadStatus
from .sources import OverlapPolicy, ReadOptions, SourceSpec
from .theme import ThemeManifest

DEFAULT_WORKSPACE = "local"


class SourceVersionRecord(BaseModel):
    """Версия настроек источника: каждое изменение — новая версия (F-163)."""

    source_id: str
    number: int
    spec: SourceSpec
    comment: str = ""
    created_at: datetime


class SourceRecord(BaseModel):
    """Источник в папке данных с текущей версией настроек."""

    id: str
    workspace_id: str = DEFAULT_WORKSPACE
    name: str
    spec: SourceSpec = Field(description="Текущая версия настроек")
    version: int
    created_at: datetime


class UploadRecord(BaseModel):
    """Загрузка в истории источника (таблица ``uploads``)."""

    id: str
    source_id: str
    seq: int
    source_version: int
    original_name: str
    sha256: str
    size: int
    format: str
    options: ReadOptions | None = None
    raw_uri: str | None = Field(None, description="Копия исходного файла; хранится до следующей загрузки")
    data_uri: str
    rejects_uri: str | None = None
    schema_snapshot: SchemaSnapshot | None = None
    cast_report: list[CastIssue] = Field(default_factory=list)
    mapping: dict[str, str] = Field(default_factory=dict)
    profile: dict[str, ColumnProfile] = Field(default_factory=dict)
    period: Period
    period_from_data: Period | None = None
    rows: int
    rows_outside_period: int = 0
    null_period_rows: int = 0
    status: UploadStatus
    review_reasons: list[str] = Field(default_factory=list)
    overlap_policy: OverlapPolicy | None = None
    data_bytes: int = 0
    uploaded_at: datetime


class ScenarioInputRef(BaseModel):
    """Вход сценария и его источник: по ним видно, какие сценарии используют источник."""

    input_id: str
    source_id: str
    main: bool = False
    scenario_id: str | None = Field(None, description="Сценарий входа (в списке «кто использует источник»)")


class ScenarioVersionRecord(BaseModel):
    """Версия сценария: каждое сохранение — новая неизменяемая версия (F-503)."""

    scenario_id: str
    number: int
    spec: ScenarioSpec
    text: str | None = Field(None, description="Сценарий текстом YAML, как его сохранили (с комментариями)")
    theme_id: str | None = Field(None, description="Шаблон оформления из папки данных")
    theme_version: int | None = Field(None, description="Версия шаблона, на которой сохранён сценарий")
    comment: str = ""
    created_at: datetime


class ScenarioRecord(BaseModel):
    """Сценарий в папке данных с текущей версией."""

    id: str
    workspace_id: str = DEFAULT_WORKSPACE
    name: str
    version: int
    current: ScenarioVersionRecord
    inputs: list[ScenarioInputRef] = Field(default_factory=list)
    created_at: datetime

    @property
    def spec(self) -> ScenarioSpec:
        return self.current.spec


class ThemeVersionRecord(BaseModel):
    """Версия шаблона оформления: файл, манифест (макеты, роли, слайды-образцы, проверка) и
    подтверждённые пользователем роли макетов. Каждый импорт и каждое подтверждение ролей —
    новая версия: запуск записывает версию шаблона, и по ней видно, с каким оформлением он шёл."""

    theme_id: str
    number: int
    pptx_uri: str = Field(description="Файл шаблона в папке данных")
    sha256: str
    original_name: str
    manifest: ThemeManifest
    roles: dict[str, str] = Field(
        default_factory=dict, description="Подтверждённые роли: роль макета → ключ макета (sldLayoutId)"
    )
    comment: str = ""
    imported_at: datetime


class ThemeRecord(BaseModel):
    """Шаблон оформления в папке данных с текущей версией."""

    id: str
    workspace_id: str = DEFAULT_WORKSPACE
    name: str
    version: int
    current: ThemeVersionRecord
    created_at: datetime


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERRORS = "errors"
    """Отчёт собран, но на слайдах из макетов есть пометки «Ошибка»."""
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunRecord(BaseModel):
    """Запуск сценария из папки данных: журнал (F-608) и запись истории запусков (F-609)."""

    id: str
    scenario_id: str
    scenario_version: int
    scenario_name: str = ""
    theme_id: str | None = None
    theme_version: int | None = None
    period: Period | None = None
    period_given: bool = Field(False, description="Отчётный период задан при запуске, а не взят по основному входу")
    source_versions: dict[str, int] = Field(default_factory=dict, description="Источник → версия настроек")
    inputs_history: dict[str, HistoryManifest] = Field(
        default_factory=dict, description="Вход → манифест истории: какие загрузки участвовали"
    )
    status: RunStatus = RunStatus.RUNNING
    result: RunResult | None = Field(None, description="Итог задания: узлы, замечания, версии окружения")
    output_uri: str | None = Field(None, description="Отчёт в папке данных")
    output_copy: str | None = Field(None, description="Копия отчёта в выбранной пользователем папке")
    trigger: str = Field("cli", description="Кто запустил: cli, app")
    started_at: datetime
    finished_at: datetime | None = None


class MetadataStore(Protocol):
    """Метаданные папки данных. Пишет их один писатель: сервер, а пока его нет — CLI под
    блокировкой папки данных (раздел 4.2, правило 8)."""

    def list_sources(self) -> list[SourceRecord]: ...

    def get_source(self, source_id: str) -> SourceRecord: ...

    def source_versions(self, source_id: str) -> list[SourceVersionRecord]: ...

    def create_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord: ...

    def update_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord: ...

    def list_uploads(self, source_id: str) -> list[UploadRecord]: ...

    def get_upload(self, upload_id: str) -> UploadRecord: ...

    def next_upload_seq(self, source_id: str) -> int: ...

    def add_upload(self, record: UploadRecord) -> None: ...

    def update_upload(self, upload_id: str, **fields: Any) -> UploadRecord: ...

    def delete_upload(self, upload_id: str) -> None: ...

    def history_manifest(self, source_id: str) -> HistoryManifest: ...

    def list_scenarios(self) -> list[ScenarioRecord]: ...

    def get_scenario(self, scenario_id: str) -> ScenarioRecord: ...

    def save_scenario(
        self,
        scenario_id: str,
        spec: ScenarioSpec,
        text: str | None = None,
        theme: tuple[str, int] | None = None,
        comment: str = "",
    ) -> ScenarioRecord: ...

    def scenario_versions(self, scenario_id: str) -> list[ScenarioVersionRecord]: ...

    def list_themes(self) -> list[ThemeRecord]: ...

    def get_theme(self, theme_id: str) -> ThemeRecord: ...

    def add_theme_version(self, record: ThemeVersionRecord, name: str | None = None) -> ThemeRecord: ...

    def theme_versions(self, theme_id: str) -> list[ThemeVersionRecord]: ...

    def add_run(self, record: RunRecord) -> None: ...

    def update_run(self, run_id: str, **fields: Any) -> RunRecord: ...

    def get_run(self, run_id: str) -> RunRecord: ...

    def list_runs(self, scenario_id: str | None = None, limit: int | None = None) -> list[RunRecord]: ...


class BlobStore(Protocol):
    """Файлы загрузок. ``uri`` — то, что уходит в манифест и задания; локально это путь."""

    def upload_uri(self, source_id: str, upload_id: str) -> str: ...

    def tmp_uri(self) -> str: ...

    def put_file(self, src: str, uri: str) -> None:
        """Скопировать файл атомарно: во временный, затем переименовать."""
        ...

    def size(self, uri: str) -> int: ...

    def delete(self, uri: str) -> None: ...

    def free_bytes(self) -> int: ...
