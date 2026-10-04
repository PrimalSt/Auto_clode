"""Папка данных приложения из своего кода, Jupyter и CLI.

Пример::

    from autogenerator.api import Home
    with Home.open(write=True) as home:
        spec, snap = home.draft_source("Продажи_2026-01.csv", "sales")
        home.create_source(spec)
        out = home.upload("sales", "Продажи_2026-01.csv")
        print(out.record.period.key, out.record.rows)
        print(home.coverage("sales").gaps)

Это ``autogenerator.home.Home`` с исполнителем в том же процессе (модуль
``autogenerator.worker``); в сервере приложения тот же ``Home`` работает с процессами-исполнителями.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from autogenerator import worker as _worker
from autogenerator.home import Home as _Home
from autogenerator.home import WorkerApi


class Home(_Home):
    """Папка данных: источники, загрузки, история, сценарии, шаблоны, запуски, резервные копии."""

    @classmethod
    def open(
        cls,
        root: str | Path | None = None,
        write: bool = False,
        create: bool = True,
        *,
        worker: WorkerApi | None = None,
        owner: str = "cli",
    ) -> Self:
        """Открыть папку данных. ``write`` — взять блокировку записи (иначе — только чтение).
        Тяжёлую работу делает ``autogenerator.worker`` в этом же процессе."""
        return super().open(root, write, create, worker=worker if worker is not None else _worker, owner=owner)
