import threading

import pytest

from autogenerator.contracts import AgenError, ErrorCode, JobStatus
from autogenerator.runner import LocalEventBus, LocalJobQueue, current_job


@pytest.fixture
def setup():
    bus = LocalEventBus()
    q = LocalJobQueue(bus)
    yield q, bus
    q.close()


def events(bus, job_id):
    sub = bus.subscribe(after=0)
    out = []
    while (e := sub.get(timeout=0.01)) is not None:
        if e.kind == "job" and e.data["id"] == job_id:
            out.append(e.data["status"])
    sub.close()
    return out


def test_job_runs_and_reports(setup):
    q, bus = setup
    seen = {}

    def fn(ctx):
        seen["current"] = current_job() is ctx
        ctx.progress("шаг", 1, 2, "steps")
        return {"answer": 42}

    info = q.submit("demo", fn, title="Пример")
    assert info.status == JobStatus.QUEUED and info.title == "Пример"
    done = q.wait(info.id, 5)
    assert done.status == JobStatus.DONE and done.result == {"answer": 42}
    assert done.progress is not None and done.progress.stage == "шаг"
    assert seen["current"] and current_job() is None
    assert events(bus, info.id)[0] == "queued" and events(bus, info.id)[-1] == "done"
    assert q.list()[0].id == info.id


def test_job_errors(setup):
    q, _ = setup

    def agen(ctx):
        raise AgenError(ErrorCode.SCHEMA_REVIEW, "нужно решение", details={"files": {}})

    def bug(ctx):
        raise RuntimeError("сломалось")

    a = q.wait(q.submit("a", agen).id, 5)
    assert a.status == JobStatus.FAILED and a.error.code == "schema_review" and a.error.details == {"files": {}}
    b = q.wait(q.submit("b", bug).id, 5)
    assert b.status == JobStatus.FAILED and b.error.code == "internal" and "RuntimeError" in b.error.message
    assert "Traceback" in b.error.details["traceback"]
    with pytest.raises(AgenError):
        q.get("job-999")


def test_cancel_queued_and_running(setup):
    q, _ = setup
    gate = threading.Event()
    started = threading.Event()

    def blocker(ctx):
        started.set()
        while not ctx.cancelled():
            gate.wait(0.01)
        raise AgenError(ErrorCode.CANCELLED, "остановлено")

    ran = []
    first = q.submit("block", blocker)
    second = q.submit("never", lambda ctx: ran.append(1))
    assert started.wait(5)
    assert q.cancel(second.id).status == JobStatus.CANCELLED
    q.cancel(first.id)
    assert q.wait(first.id, 5).status == JobStatus.CANCELLED
    assert q.wait(second.id, 5).status == JobStatus.CANCELLED and not ran


def test_lanes_run_in_parallel(setup):
    q, _ = setup
    gate = threading.Event()
    slow = q.submit("slow", lambda ctx: gate.wait(5), lane="main")
    fast = q.wait(q.submit("fast", lambda ctx: "ok", lane="light").id, 5)
    assert fast.status == JobStatus.DONE and q.get(slow.id).status == JobStatus.RUNNING
    gate.set()
    assert q.wait(slow.id, 5).status == JobStatus.DONE
    with pytest.raises(AgenError):
        q.submit("x", lambda ctx: None, lane="nope")
