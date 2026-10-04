"""Состояние сервера: папка данных, очередь заданий, поток событий и два исполнителя.

Папку данных сервер держит открытой на запись всё время работы (блокировка ``server.lock``):
он единственный писатель метаданных. Запросы окна и задания очереди работают с одним
``Home`` из разных потоков; SQLite в режиме WAL это допускает, а запись ждёт своей очереди
(``busy_timeout``). Восстановление базы из копии меняет саму базу, поэтому идёт, только когда
нет ни заданий, ни других запросов (``Gate``).
"""

from __future__ import annotations

import contextlib
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

from autogenerator.contracts import AgenError, ErrorCode, JobContext, JobInfo, PluginManifest
from autogenerator.home import Home
from autogenerator.runner import LocalEventBus, LocalJobQueue, ProcessExecutor

from .workers import RemoteWorker

MAIN_TIMEOUT = 30 * 60
"""Таймаут вызова исполнителя загрузок и запусков (с), раздел 6.6."""
LIGHT_TIMEOUT = 180
"""Таймаут вызова исполнителя превью (с): пробная сборка слайда с картинкой ждёт PowerPoint до 120 с."""
GATE_TIMEOUT = 10.0
"""Сколько восстановление базы ждёт, пока закончатся запросы (с)."""


@dataclass
class Settings:
    """Настройки сервера: папка данных, токен, кому разрешены запросы."""

    home: str | Path | None = None
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    origins: list[str] = field(default_factory=list)
    """Origin окна оболочки (CORS); в режиме разработки — ещё адрес dev-сервера интерфейса."""
    hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    """Допустимые заголовки Host: защита от подмены DNS (DNS rebinding)."""
    worker: str = "autogenerator.worker"
    """Модуль исполнителя, который процессы-исполнители импортируют по имени."""
    main_timeout: float | None = MAIN_TIMEOUT
    light_timeout: float | None = LIGHT_TIMEOUT
    prestart: bool = True
    """Запустить исполнители сразу (в фоне), чтобы первое задание не ждало импорта модулей."""
    ui: Path | None = None
    """Папка собранного интерфейса (``frontend``); по умолчанию — ``ui`` рядом с пакетом, если она есть."""


class Gate:
    """Обычные запросы и задания проходят вместе; восстановление базы — одно, когда никого нет."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._shared = 0
        self._exclusive = False

    @contextmanager
    def shared(self) -> Iterator[None]:
        with self._cond:
            self._cond.wait_for(lambda: not self._exclusive)
            self._shared += 1
        try:
            yield
        finally:
            with self._cond:
                self._shared -= 1
                self._cond.notify_all()

    @contextmanager
    def exclusive(self, timeout: float = GATE_TIMEOUT) -> Iterator[None]:
        with self._cond:
            if not self._cond.wait_for(lambda: not self._exclusive, timeout):
                raise _busy()
            self._exclusive = True
            if not self._cond.wait_for(lambda: self._shared == 0, timeout):
                self._exclusive = False
                self._cond.notify_all()
                raise _busy()
        try:
            yield
        finally:
            with self._cond:
                self._exclusive = False
                self._cond.notify_all()


def _busy() -> AgenError:
    return AgenError(
        ErrorCode.IN_USE,
        "Папка данных занята: идут задания или запросы",
        hint="Дождитесь окончания заданий и повторите.",
    )


class ServerState:
    """Всё, с чем работают разделы API."""

    def __init__(
        self,
        settings: Settings,
        home: Home,
        bus: LocalEventBus,
        jobs: LocalJobQueue,
        executors: dict[str, ProcessExecutor],
    ):
        self.settings = settings
        self.home = home
        self.bus = bus
        self.jobs = jobs
        self.executors = executors
        self.gate = Gate()
        self.started_at = datetime.now(UTC).replace(microsecond=0)
        self.shutdown: Callable[[], None] | None = None
        """Остановить сервер (задаёт тот, кто его запустил: ``__main__``)."""
        self._manifest: PluginManifest | None = None

    @classmethod
    def open(cls, settings: Settings) -> ServerState:
        """Открыть папку данных на запись и запустить очередь; исполнители запускаются в фоне."""
        executors = {
            "main": ProcessExecutor(settings.worker, "main", timeout=settings.main_timeout),
            "light": ProcessExecutor(settings.worker, "light", timeout=settings.light_timeout),
        }
        worker = RemoteWorker(executors["main"], executors["light"])
        home = Home.open(settings.home, write=True, worker=worker, owner="приложение Autogenerator")
        bus = LocalEventBus()
        state = cls(settings, home, bus, LocalJobQueue(bus), executors)
        if settings.prestart:
            for ex in executors.values():
                threading.Thread(target=_prestart, args=(ex,), name=f"agen-start-{ex.name}", daemon=True).start()
        return state

    def close(self) -> None:
        self.jobs.close()
        for ex in self.executors.values():
            ex.close()
        self.home.close()

    # --- задания и события -----------------------------------------------------------

    def submit(
        self,
        kind: str,
        fn: Callable[[Home, JobContext], Any],
        *,
        lane: str = "main",
        title: str = "",
        changed: Callable[[Any], list[tuple[str, str | None]]] | None = None,
    ) -> JobInfo:
        """Поставить задание. ``fn(home, ctx)`` выполняется в потоке очереди; итог уходит в
        задание как JSON. ``changed(итог)`` — что изменилось в данных (для события ``changed``)."""

        def job(ctx: JobContext) -> Any:
            with self.gate.shared():
                result = fn(self.home, ctx)
            for what, ident in changed(result) if changed is not None else []:
                self.changed(what, ident)
            return jsonable_encoder(result)

        return self.jobs.submit(kind, job, lane=lane, title=title)

    def changed(self, what: str, ident: str | None = None) -> None:
        """Событие для окна: данные раздела ``what`` (sources, uploads, scenarios, themes, runs,
        all) изменились — перечитать."""
        self.bus.publish("changed", {"what": what, "id": ident})

    def plugin_manifest(self, refresh: bool = False) -> PluginManifest:
        """Манифест модулей и плагинов из исполнителя превью; запоминается до перезапуска
        исполнителей. Ошибку (исполнитель не запустился) не запоминает."""
        if refresh or self._manifest is None:
            self._manifest = self.home.worker.plugin_manifest()
        return self._manifest

    def busy(self) -> bool:
        """Идут или ждут задания."""
        return any(not j.status.finished for j in self.jobs.list())


def _prestart(ex: ProcessExecutor) -> None:
    with contextlib.suppress(AgenError):  # ошибка запуска видна в info() исполнителя и на экране «Модули»
        ex.start()
