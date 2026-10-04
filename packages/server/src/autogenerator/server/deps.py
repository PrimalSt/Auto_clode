"""Общее для разделов API: состояние сервера, папка данных, задания."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from fastapi.responses import FileResponse

from autogenerator.contracts import JobInfo, Period, ScenarioSpec
from autogenerator.contracts.yaml_io import loads_yaml
from autogenerator.home import Home

from .state import ServerState

WAIT_MAX = 300.0
"""Дольше этого запрос задания не ждёт его конца (с): дальше — по событиям или ``GET /api/jobs/{id}``."""


def get_state(request: Request) -> ServerState:
    state: ServerState = request.app.state.agen
    return state


StateDep = Annotated[ServerState, Depends(get_state)]


def get_home(state: StateDep) -> Iterator[Home]:
    """Папка данных на время запроса (восстановление базы ждёт, пока запрос не закончится)."""
    with state.gate.shared():
        yield state.home


HomeDep = Annotated[Home, Depends(get_home)]

Wait = Annotated[
    float | None,
    Query(
        ge=0,
        description="Подождать конца задания до стольких секунд (не больше 300) и вернуть его итог; "
        "без параметра задание возвращается сразу, ход — в потоке событий",
    ),
]


def job_reply(state: ServerState, info: JobInfo, wait: float | None) -> JobInfo:
    if wait:
        return state.jobs.wait(info.id, min(wait, WAIT_MAX))
    return info


def period(value: str | Period | None) -> Period | None:
    return Period.parse(value) if isinstance(value, str) else value


def draft(home: Home, text: str | None, spec: dict[str, Any] | None) -> ScenarioSpec:
    """Черновик сценария из окна: текст YAML (редактор кода) или JSON той же схемы (формы)."""
    if text is not None:
        return home.worker.load_scenario(loads_yaml(text, "черновик"), "черновик")
    return home.worker.load_scenario(spec or {}, "черновик")


def file_reply(path: Path, media: str | None = None) -> FileResponse:
    return FileResponse(path, media_type=media, filename=path.name)
