"""Очередь заданий, поток событий и исполнители (ARCHITECTURE.md, разделы 4.2 п. 7 и 6.6).

Сервер ставит задание в очередь (``JobQueue``); задание выполняется в потоке сервера и
отдаёт тяжёлую работу исполнителю (``ExecutorBackend``) — отдельному процессу с модулями
обработки. Ход заданий идёт в поток событий (``EventBus``), а оттуда — в окно (SSE).
Локальные реализации — в пакете ``runner``; в серверном режиме их заменят очередь и шина
на Redis или PostgreSQL и воркеры очереди.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .plugins import ProgressCallback


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


class JobProgress(BaseModel):
    """Ход задания: этап и сколько сделано (``total`` пусто — неизвестно)."""

    stage: str
    done: int = 0
    total: int | None = None
    unit: str = "bytes"


class JobError(BaseModel):
    """Ошибка задания так, как её показать: код из ``ErrorCode``, текст, подсказка, подробности
    (например, сверка структуры по файлам у ``schema_review``)."""

    code: str
    message: str
    hint: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class JobInfo(BaseModel):
    """Задание в очереди: что делает, в каком состоянии, итог или ошибка."""

    id: str
    kind: str = Field(description="Тип задания: upload, run, import_theme, draft_source…")
    title: str = ""
    lane: str = Field("main", description="Очередь: main — загрузки и запуски, light — быстрые задания")
    status: JobStatus = JobStatus.QUEUED
    progress: JobProgress | None = None
    result: Any = Field(None, description="Итог задания (JSON); вид зависит от kind")
    error: JobError | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ServerEvent(BaseModel):
    """Событие для окна: ход заданий (``job``) и изменения данных (``changed``)."""

    id: int = Field(description="Номер события по порядку: по нему поток продолжается после переподключения")
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    at: datetime


class JobContext(Protocol):
    """То, что видит выполняющееся задание."""

    @property
    def job_id(self) -> str: ...

    def progress(self, stage: str, done: int = 0, total: int | None = None, unit: str = "bytes") -> None: ...

    def cancelled(self) -> bool: ...

    @property
    def progress_callback(self) -> ProgressCallback: ...


class JobQueue(Protocol):
    def submit(self, kind: str, fn: Callable[[JobContext], Any], *, lane: str = "main", title: str = "") -> JobInfo:
        """Поставить задание; ``fn`` выполнится в потоке очереди ``lane`` и вернёт итог (JSON)."""
        ...

    def get(self, job_id: str) -> JobInfo: ...

    def list(self, limit: int | None = None) -> list[JobInfo]: ...

    def cancel(self, job_id: str) -> JobInfo: ...


class EventBus(Protocol):
    def publish(self, kind: str, data: dict[str, Any]) -> ServerEvent: ...

    def subscribe(self, after: int | None = None) -> EventSubscription:
        """Подписка; ``after`` — номер последнего полученного события (после переподключения
        придут пропущенные, если они ещё в буфере)."""
        ...


class EventSubscription(Protocol):
    def get(self, timeout: float | None = None) -> ServerEvent | None: ...

    @property
    def closed(self) -> bool:
        """Подписка закрыта (в том числе шиной — подписчик не успевал читать)."""
        ...

    def close(self) -> None: ...


class ExecutorInfo(BaseModel):
    name: str
    pid: int | None = None
    alive: bool = False
    calls: int = Field(0, description="Сколько вызовов выполнил текущий процесс")
    restarts: int = 0
    error: str | None = Field(None, description="Почему исполнитель не запустился (например, ошибка импорта модуля)")


class ExecutorBackend(Protocol):
    """Исполнитель: вызывает функцию модуля исполнителя (``worker``) в отдельном процессе.
    Аргументы и итог — данные (модели контрактов, пути, числа); функции ``progress`` и
    ``cancelled`` среди ``kwargs`` работают через границу процессов. ``cancelled`` самого
    вызова останавливает его, даже если функция отмену не проверяет."""

    def call(
        self,
        fn: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        cancelled: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> Any: ...

    def info(self) -> ExecutorInfo: ...

    def close(self) -> None: ...
