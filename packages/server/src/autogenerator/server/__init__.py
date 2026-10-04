"""Локальный сервер приложения (ARCHITECTURE.md, разделы 6.8, 10 и 12): REST API и поток
событий для окна, очередь заданий, единственный писатель метаданных папки данных.

Сервер не импортирует модули обработки и плагины: тяжёлую работу делают процессы-исполнители
(``runner.ProcessExecutor``), которые загружают ``autogenerator.worker`` по имени.

Импорт пакета ничего не загружает (процессы-исполнители тоже его импортируют при запуске
через ``python -m autogenerator.server``); ``create_app``, ``ServerState``, ``Settings`` и
``main`` загружаются при первом обращении.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import create_app
    from .serve import main
    from .state import ServerState, Settings

__all__ = ["ServerState", "Settings", "create_app", "main"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "main":
        from .serve import main

        return main
    if name in ("ServerState", "Settings"):
        from . import state

        return getattr(state, name)
    raise AttributeError(name)
