"""Конвейер в потоках: пока одна порция приводится к типам, следующая уже читается, а
предыдущая пишется на диск. Polars и pyarrow отпускают GIL, поэтому этапы действительно
идут одновременно, а память ограничена глубиной очередей."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator

_DONE = object()


class _Failure:
    def __init__(self, error: BaseException):
        self.error = error


def prefetch[T](source: Iterator[T], depth: int = 2) -> Iterator[T]:
    """Выполнять итератор в отдельном потоке, держа наготове до ``depth`` элементов.
    Исключение источника поднимается у потребителя; если потребитель прервался, поток
    останавливается и источник закрывается."""
    q: queue.Queue[object] = queue.Queue(maxsize=depth)
    stop = threading.Event()

    def put(item: object) -> bool:
        while not stop.is_set():
            try:
                q.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def run() -> None:
        try:
            for item in source:
                if not put(item):
                    break
        except BaseException as e:
            put(_Failure(e))
            return
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()
        put(_DONE)

    t = threading.Thread(target=run, name="agen-prefetch", daemon=True)
    t.start()
    try:
        while True:
            item = q.get()
            if item is _DONE:
                return
            if isinstance(item, _Failure):
                raise item.error
            yield item  # type: ignore[misc]
    finally:
        stop.set()
        t.join(timeout=30)


class BackgroundWriter:
    """Задачи записи в отдельном потоке по очереди, не больше ``depth`` в ожидании.
    Первая ошибка записи поднимается при следующей постановке или при ``close``."""

    def __init__(self, depth: int = 2):
        self._q: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=depth)
        self._error: BaseException | None = None
        self._t = threading.Thread(target=self._run, name="agen-writer", daemon=True)
        self._t.start()

    def _run(self) -> None:
        while True:
            task = self._q.get()
            if task is None:
                return
            if self._error is not None:
                continue
            try:
                task()
            except BaseException as e:
                self._error = e

    def submit(self, task: Callable[[], None]) -> None:
        if self._error is not None:
            raise self._error
        self._q.put(task)

    def close(self) -> None:
        """Дождаться записи всего поставленного."""
        self._q.put(None)
        self._t.join()
        if self._error is not None:
            raise self._error

    def abort(self) -> None:
        """Остановить без ожидания результата (при отмене или ошибке)."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._q.put(None)
        self._t.join(timeout=60)
