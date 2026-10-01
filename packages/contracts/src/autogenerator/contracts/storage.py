"""Записи и интерфейсы хранилищ (ARCHITECTURE.md, раздел 4.2, правило 7; раздел 9).

Метаданные (источники, их версии, загрузки) хранит ``MetadataStore``, файлы загрузок —
``BlobStore``. Для одного пользователя на компьютере это SQLite и папка на диске (пакет
``storage``); для сервера реализации заменяются, а модули обработки не меняются: они видят
только манифесты и URI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .history import HistoryManifest
from .periods import Period
from .snapshot import CastIssue, ColumnProfile, SchemaSnapshot, UploadStatus
from .sources import OverlapPolicy, ReadOptions, SourceSpec

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
