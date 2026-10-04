"""Задания и поток событий (SSE).

Поток ``/api/events`` отдаёт события ``job`` (ход, итог и ошибка заданий, без самого итога —
он в ``GET /api/jobs/{id}``) и ``changed`` (данные раздела изменились — перечитать). Каждое
событие с номером: после переподключения окно получает пропущенные (заголовок
``Last-Event-ID`` или ``?after=``), пока они есть в буфере. Раз в 15 секунд идёт пустая строка-
комментарий, чтобы соединение не закрывали по простою.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from autogenerator.contracts import JobInfo, ServerEvent

from ..deps import StateDep, Wait, job_reply

router = APIRouter(tags=["задания"])

PING_EVERY = 15.0
POLL = 1.0


@router.get("/api/jobs")
def list_jobs(state: StateDep, limit: Annotated[int | None, Query(ge=1)] = None) -> list[JobInfo]:
    """Задания этого запуска сервера, новые первыми."""
    return state.jobs.list(limit)


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, state: StateDep, wait: Wait = None) -> JobInfo:
    return job_reply(state, state.jobs.get(job_id), wait)


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, state: StateDep) -> JobInfo:
    """Отменить задание: ещё не начатое — сразу, идущее — остановить исполнитель."""
    return state.jobs.cancel(job_id)


def _sse(event: ServerEvent) -> str:
    data = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"id: {event.id}\nevent: {event.kind}\ndata: {data}\n\n"


@router.get("/api/events", response_class=StreamingResponse)
async def events(
    request: Request,
    state: StateDep,
    after: Annotated[int | None, Query(description="Номер последнего полученного события")] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    if after is None and last_event_id and last_event_id.isdigit():
        after = int(last_event_id)
    sub = state.bus.subscribe(after)

    async def stream() -> AsyncIterator[str]:
        idle = 0.0
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                event = await anyio.to_thread.run_sync(sub.get, POLL)
                if event is not None:
                    idle = 0.0
                    yield _sse(event)
                    continue
                if sub.closed:
                    break  # подписчик отстал и отключён: окно переподключится и догонит по номеру
                idle += POLL
                if idle >= PING_EVERY:
                    idle = 0.0
                    yield ": ping\n\n"
        finally:
            sub.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
