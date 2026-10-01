"""Чтение наперёд в отдельном потоке: пока Polars разбирает одну порцию (без GIL), поток
готовит следующую — читает с диска и перекодирует."""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator

_DONE = object()


class _Failure:
    def __init__(self, error: BaseException):
        self.error = error


def read_ahead[T](source: Iterator[T], depth: int = 2) -> Iterator[T]:
    """Выполнять итератор в отдельном потоке, держа наготове до ``depth`` элементов."""
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

    t = threading.Thread(target=run, name="agen-read-ahead", daemon=True)
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
