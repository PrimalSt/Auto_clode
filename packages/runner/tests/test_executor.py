import os

import pytest

from autogenerator.contracts import AgenError, ErrorCode, ReadProgress
from autogenerator.runner import ProcessExecutor

TARGET = "autogenerator.runner.selftest"


@pytest.fixture
def ex():
    e = ProcessExecutor(TARGET, "test", cancel_grace=1.0)
    yield e
    e.close()


def test_call_in_other_process(ex):
    assert ex.call("echo", ({"a": [1, 2]},)) == {"a": [1, 2]}
    child = ex.call("pid")
    assert child != os.getpid()
    info = ex.info()
    assert info.alive and info.pid == child and info.calls == 2


def test_progress_crosses_processes(ex):
    seen: list[ReadProgress] = []
    assert ex.call("slow", (0.1,), {"progress": seen.append}) == 10
    assert [p.done for p in seen] == list(range(10)) and seen[0].stage == "работа"
    seen.clear()
    assert ex.call("progress_from_thread", (), {"progress": seen.append}) == 2
    assert [p.stage for p in seen] == ["фон", "готово"]


def test_errors(ex):
    with pytest.raises(AgenError) as e:
        ex.call("fail", ("плохо",))
    assert e.value.code == ErrorCode.NODE_FAILED and e.value.hint == "подсказка" and e.value.details == {"node": "x"}
    with pytest.raises(ValueError, match="ошибка в коде") as v:
        ex.call("bug")
    assert any("Traceback" in n for n in v.value.__notes__)
    with pytest.raises(AgenError) as e:
        ex.call("odd_error")
    assert e.value.code == ErrorCode.WORKER_FAILED and "_Unpicklable" in e.value.message
    with pytest.raises(AgenError) as e:
        ex.call("lock")
    assert e.value.code == ErrorCode.WORKER_FAILED and "pickle" in e.value.message
    assert ex.call("echo", (1,)) == 1  # процесс жив после ошибок
    assert ex.info().restarts == 0


def test_crash_restarts(ex):
    first = ex.call("pid")
    with pytest.raises(AgenError) as e:
        ex.call("crash", (3,))
    assert e.value.code == ErrorCode.WORKER_FAILED and "код выхода 3" in e.value.message
    second = ex.call("pid")
    assert second != first and ex.info().restarts == 1


def test_timeout_kills(ex):
    first = ex.call("pid")
    with pytest.raises(AgenError) as e:
        ex.call("stubborn", (30,), timeout=0.5)
    assert e.value.code == ErrorCode.TIMEOUT
    assert ex.call("pid") != first


def test_cancel_honoured_and_forced(ex):
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    first = ex.call("pid")
    with pytest.raises(AgenError) as e:
        ex.call("slow", (5.0,), {"cancelled": cancelled})
    assert e.value.code == ErrorCode.CANCELLED and e.value.message == "Отменено исполнителем"
    assert ex.call("pid") == first  # исполнитель сам остановился — процесс тот же
    with pytest.raises(AgenError) as e:
        ex.call("stubborn", (30,), cancelled=lambda: True)
    assert e.value.code == ErrorCode.CANCELLED and e.value.message == "Задание отменено"
    assert ex.call("pid") != first


def test_broken_module_does_not_break_caller():
    ex = ProcessExecutor("autogenerator.runner.no_such_module", "broken")
    with pytest.raises(AgenError) as e:
        ex.call("echo", (1,))
    assert e.value.code == ErrorCode.WORKER_FAILED
    assert "no_such_module" in e.value.details["traceback"]
    assert ex.info().error and not ex.info().alive
    ex.close()


def test_restart_after_max_calls():
    ex = ProcessExecutor(TARGET, "short", max_calls=2)
    try:
        a = ex.call("pid")
        assert ex.call("pid") == a
        assert ex.call("pid") != a
    finally:
        ex.close()
