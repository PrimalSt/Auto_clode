"""Очередь заданий, поток событий и процессы-исполнители — локальные реализации
``JobQueue``, ``EventBus`` и ``ExecutorBackend`` из ``contracts`` (ARCHITECTURE.md, раздел 6.6).

Пакет зависит только от контрактов. Модуль исполнителя (``autogenerator.worker``) процесс-
исполнитель импортирует по имени, поэтому сервер его не загружает.
"""

from .events import LocalEventBus, Subscription
from .executor import ProcessExecutor
from .jobs import LocalJobQueue, current_job

__all__ = ["LocalEventBus", "LocalJobQueue", "ProcessExecutor", "Subscription", "current_job"]
