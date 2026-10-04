"""Модуль исполнителя для проверки самого исполнителя (тесты пакета и сервера): эхо, долгий
вызов с ходом и отменой, падение процесса, ошибки."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any

from autogenerator.contracts import AgenError, ErrorCode, ProgressCallback, ReadProgress


def echo(value: Any) -> Any:
    return value


def pid() -> int:
    return os.getpid()


def slow(
    seconds: float,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
    steps: int = 10,
) -> int:
    """Работает ``seconds`` секунд, сообщает ход; при отмене бросает ``CANCELLED``."""
    for i in range(steps):
        if cancelled is not None and cancelled():
            raise AgenError(ErrorCode.CANCELLED, "Отменено исполнителем")
        if progress is not None:
            progress(ReadProgress("работа", i, steps, "steps"))
        time.sleep(seconds / steps)
    return steps


def stubborn(seconds: float) -> None:
    """Долгий вызов, который не проверяет отмену."""
    time.sleep(seconds)


def progress_from_thread(progress: ProgressCallback | None = None) -> int:
    """Ход работы из фонового потока (как запись Parquet в фоне)."""
    t = threading.Thread(target=lambda: progress and progress(ReadProgress("фон", 1, 2, "steps")))
    t.start()
    t.join()
    if progress is not None:
        progress(ReadProgress("готово", 2, 2, "steps"))
    return 2


def crash(code: int = 3) -> None:
    os._exit(code)


def fail(message: str = "не получилось") -> None:
    raise AgenError(ErrorCode.NODE_FAILED, message, hint="подсказка", details={"node": "x"})


def bug() -> None:
    raise ValueError("ошибка в коде")


class _Unpicklable(Exception):
    def __init__(self, a: int, b: int):
        super().__init__(a)
        self.lock = threading.Lock()


def odd_error() -> None:
    raise _Unpicklable(1, 2)


def lock() -> threading.Lock:
    """Итог, который нельзя передать через pickle."""
    return threading.Lock()


_dirty = False


def dirty(fail: bool = False) -> int:
    """Вызов, после которого модуль просит перезапустить процесс (как после кода пользователя)."""
    global _dirty
    _dirty = True
    if fail:
        raise AgenError(ErrorCode.USER_CODE, "ошибка в коде пользователя")
    return os.getpid()


def restart_requested() -> bool:
    global _dirty
    asked, _dirty = _dirty, False
    return asked
