"""Очередь заданий в процессе сервера (``JobQueue``).

У каждой очереди (``lane``) свой поток: по умолчанию ``main`` — загрузки, запуски и импорт
шаблонов (по одному, как и их исполнитель), ``light`` — быстрые задания. Задание — функция
``fn(ctx)``: она пишет метаданные в процессе сервера, а тяжёлую работу отдаёт исполнителю.
Ход, итог и ошибка задания идут в поток событий (вид события ``job``).

Задания живут в памяти сервера: после перезапуска сервера незавершённые запуски помечаются
прерванными в метаданных (``interrupt_running``), а сама очередь начинается заново.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    EventBus,
    JobContext,
    JobError,
    JobInfo,
    JobProgress,
    JobStatus,
    ProgressCallback,
    ReadProgress,
)

PROGRESS_EVERY = 0.25
"""Не чаще, чем раз в столько секунд, ход задания уходит в поток событий."""

_local = threading.local()


def current_job() -> JobContext | None:
    """Задание, которое выполняется в этом потоке (``None`` — вне очереди)."""
    return getattr(_local, "job", None)


def _now() -> datetime:
    return datetime.now(UTC)


class _Job:
    def __init__(self, info: JobInfo, fn: Callable[[JobContext], Any]):
        self.info = info
        self.fn = fn
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.published = 0.0


class _Context:
    def __init__(self, q: LocalJobQueue, job: _Job):
        self._q = q
        self._job = job

    @property
    def job_id(self) -> str:
        return self._job.info.id

    def progress(self, stage: str, done: int = 0, total: int | None = None, unit: str = "bytes") -> None:
        self._q._progress(self._job, JobProgress(stage=stage, done=done, total=total, unit=unit))

    def cancelled(self) -> bool:
        return self._job.cancel.is_set()

    @property
    def progress_callback(self) -> ProgressCallback:
        def report(p: ReadProgress) -> None:
            self.progress(p.stage, p.done, p.total, p.unit)

        return report


class LocalJobQueue:
    def __init__(self, bus: EventBus | None = None, lanes: Sequence[str] = ("main", "light"), keep: int = 200):
        self._bus = bus
        self._keep = keep
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._ids = itertools.count(1)
        self._queues: dict[str, queue.Queue[_Job | None]] = {lane: queue.Queue() for lane in lanes}
        self._threads = [
            threading.Thread(target=self._loop, args=(lane,), name=f"agen-jobs-{lane}", daemon=True) for lane in lanes
        ]
        for t in self._threads:
            t.start()

    # --- JobQueue ------------------------------------------------------------------

    def submit(self, kind: str, fn: Callable[[JobContext], Any], *, lane: str = "main", title: str = "") -> JobInfo:
        if lane not in self._queues:
            raise AgenError(ErrorCode.SPEC_INVALID, f"Нет очереди заданий «{lane}»")
        with self._lock:
            info = JobInfo(id=f"job-{next(self._ids)}", kind=kind, title=title, lane=lane, created_at=_now())
            job = _Job(info, fn)
            self._jobs[info.id] = job
            self._prune()
            snapshot = info.model_copy()
        self._publish(job)
        self._queues[lane].put(job)
        return snapshot

    def get(self, job_id: str) -> JobInfo:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise AgenError(ErrorCode.NOT_FOUND, f"Задания «{job_id}» нет")
            return job.info.model_copy()

    def list(self, limit: int | None = None) -> list[JobInfo]:
        """Задания, новые первыми."""
        with self._lock:
            jobs = [j.info.model_copy() for j in reversed(self._jobs.values())]
        return jobs[:limit] if limit else jobs

    def cancel(self, job_id: str) -> JobInfo:
        """Отменить задание: ещё не начатое — сразу, идущее — попросить остановиться (исполнитель
        завершит работу или будет остановлен)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise AgenError(ErrorCode.NOT_FOUND, f"Задания «{job_id}» нет")
            job.cancel.set()
            queued = job.info.status == JobStatus.QUEUED
            if queued:
                job.info.status = JobStatus.CANCELLED
                job.info.finished_at = _now()
                job.done.set()
            snapshot = job.info.model_copy()
        if queued:
            self._publish(job)
        return snapshot

    def wait(self, job_id: str, timeout: float | None = None) -> JobInfo:
        """Дождаться конца задания (для тестов и CLI)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise AgenError(ErrorCode.NOT_FOUND, f"Задания «{job_id}» нет")
        job.done.wait(timeout)
        return self.get(job_id)

    def close(self, timeout: float = 5.0) -> None:
        """Остановить потоки очереди; незапущенные задания отменяются."""
        for q in self._queues.values():
            q.put(None)
        for t in self._threads:
            t.join(timeout)

    # --- внутреннее ----------------------------------------------------------------

    def _loop(self, lane: str) -> None:
        q = self._queues[lane]
        while True:
            job = q.get()
            if job is None:
                break
            with self._lock:
                if job.info.status != JobStatus.QUEUED:
                    continue  # отменено до начала
                job.info.status = JobStatus.RUNNING
                job.info.started_at = _now()
            self._publish(job)
            _local.job = _Context(self, job)
            try:
                result = job.fn(_local.job)
                status, error = JobStatus.DONE, None
            except AgenError as e:
                result = None
                status = JobStatus.CANCELLED if e.code == ErrorCode.CANCELLED else JobStatus.FAILED
                error = JobError(code=str(e.code), message=e.message, hint=e.hint, details=e.details)
            except Exception as e:
                result = None
                status = JobStatus.FAILED
                error = JobError(
                    code="internal", message=f"{type(e).__name__}: {e}", details={"traceback": traceback.format_exc()}
                )
            finally:
                _local.job = None
            with self._lock:
                job.info.status = status
                job.info.result = result
                job.info.error = error
                job.info.finished_at = _now()
            job.done.set()
            self._publish(job)

    def _progress(self, job: _Job, progress: JobProgress) -> None:
        with self._lock:
            job.info.progress = progress
        now = time.monotonic()
        if now - job.published >= PROGRESS_EVERY or (progress.total is not None and progress.done >= progress.total):
            self._publish(job)

    def _publish(self, job: _Job) -> None:
        job.published = time.monotonic()
        if self._bus is None:
            return
        with self._lock:
            data = job.info.model_dump(mode="json", exclude={"result"})
        self._bus.publish("job", data)

    def _prune(self) -> None:
        finished = [k for k, j in self._jobs.items() if j.info.status.finished]
        for k in finished[: max(0, len(finished) - self._keep)]:
            del self._jobs[k]
