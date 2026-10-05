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


def test_restart_when_module_asks(ex):
    # После вызова с кодом пользователя модуль просит перезапуск: следующий вызов — в новом процессе,
    # и он запущен заранее, в фоне. Обычные вызовы процесс не меняют.
    first = ex.call("pid")
    assert ex.call("pid") == first
    assert ex.call("dirty") == first
    second = ex.call("pid")
    assert second != first and ex.info().restarts == 1
    with pytest.raises(AgenError) as e:
        ex.call("dirty", (True,))
    assert e.value.code == ErrorCode.USER_CODE
    assert ex.call("pid") not in (first, second) and ex.info().restarts == 2


def test_output_without_console_goes_to_the_executor_log(tmp_path, monkeypatch):
    # pythonw (установленное приложение под оболочкой): потоков вывода нет, печать — в журнал
    from autogenerator.runner import executor

    log = tmp_path / "executor-main.log"
    log.write_bytes(b"x" * (executor.LOG_LIMIT + 1))
    monkeypatch.setenv("AGEN_EXECUTOR_LOG", str(log))
    sink = executor._output_sink()
    try:
        print("печать модуля", file=sink)
    finally:
        sink.close()
    assert log.read_text(encoding="utf-8") == "печать модуля\n"
    assert (tmp_path / "executor-main.log.1").stat().st_size == executor.LOG_LIMIT + 1
    monkeypatch.delenv("AGEN_EXECUTOR_LOG")
    with executor._output_sink() as nowhere:
        assert nowhere.name == os.devnull
