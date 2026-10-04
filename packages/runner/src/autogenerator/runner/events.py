"""Поток событий в процессе сервера (``EventBus``): ход заданий и изменения данных для окна.

События нумеруются по порядку и последние держатся в буфере: окно, которое переподключилось
(``Last-Event-ID``), получает пропущенное. Подписчик, который не успевает читать, отключается —
окно переподключится и догонит по номеру.
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from autogenerator.contracts import ServerEvent

SUBSCRIBER_QUEUE = 5000


class Subscription:
    """Подписка на события; ``get`` ждёт следующее событие, ``None`` — таймаут."""

    def __init__(self, bus: LocalEventBus):
        self._bus = bus
        self._queue: queue.Queue[ServerEvent | None] = queue.Queue(SUBSCRIBER_QUEUE)
        self.closed = False

    def _put(self, event: ServerEvent) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            return False

    def get(self, timeout: float | None = None) -> ServerEvent | None:
        if self.closed and self._queue.empty():
            return None
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._bus._drop(self)
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)  # разбудить того, кто ждёт


class LocalEventBus:
    def __init__(self, buffer: int = 1000):
        self._lock = threading.Lock()
        self._buffer: deque[ServerEvent] = deque(maxlen=buffer)
        self._subs: list[Subscription] = []
        self._next = 1

    def publish(self, kind: str, data: dict[str, Any]) -> ServerEvent:
        with self._lock:
            event = ServerEvent(id=self._next, kind=kind, data=data, at=datetime.now(UTC))
            self._next += 1
            self._buffer.append(event)
            lagging = [s for s in self._subs if not s._put(event)]
        for s in lagging:
            s.close()
        return event

    def subscribe(self, after: int | None = None) -> Subscription:
        sub = Subscription(self)
        with self._lock:
            if after is not None:
                for event in self._buffer:
                    if event.id > after:
                        sub._put(event)
            self._subs.append(sub)
        return sub

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next - 1

    def _drop(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
