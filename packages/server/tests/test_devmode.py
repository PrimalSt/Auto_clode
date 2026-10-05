"""Режим разработчика: изменённые модули проверяются тестами, потом применяются."""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autogenerator.contracts import AgenError, ErrorCode, JobContext
from autogenerator.server import ServerState, Settings, create_app, devmode

AUTH = {"Authorization": "Bearer t"}


def _module(root: Path, name: str, test: str | None) -> Path:
    src = root / "packages" / name / "src" / name
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    if test is not None:
        tests = root / "packages" / name / "tests"
        tests.mkdir()
        (tests / f"test_{name}.py").write_text(test, encoding="utf-8")
    return src / "__init__.py"


def test_source_root_is_found_only_in_a_checkout(tmp_path: Path):
    root = devmode.source_root()
    assert root is not None and (root / "packages" / "server").is_dir()
    assert devmode.source_root(tmp_path / "a" / "b.py") is None


def test_changed_modules_and_their_tests(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    old = _module(tmp_path, "good", "def test_ok():\n    assert True\n")
    _module(tmp_path, "bad", "def test_fails():\n    assert 1 == 2\n")
    _module(tmp_path, "bare", None)
    since = time.time() - 60
    os.utime(old, (since - 60, since - 60))
    assert devmode.changed_modules(tmp_path, since) == ["bad", "bare"]

    checks = {c.module: c for c in devmode.check_modules(tmp_path, ["good", "bad", "bare"])}
    assert checks["good"].ok and "1 passed" in checks["good"].output
    assert not checks["bad"].ok and "1 failed" in checks["bad"].output
    assert checks["bare"].ok and checks["bare"].output == "тестов нет"


def test_check_endpoint_needs_dev_mode(tmp_path: Path):
    for dev in (False, True):
        settings = Settings(home=tmp_path / f"home-{dev}", token="t", hosts=["testserver"], prestart=False, dev=dev)
        state = ServerState.open(settings)
        try:
            c = TestClient(create_app(state, dev=dev))
            system = c.get("/api/system", headers=AUTH).json()
            assert system["dev"] is dev
            assert (system["dev_source"] is not None) is dev
            r = c.post("/api/modules/check", json={"modules": []}, headers=AUTH)
            if not dev:
                assert r.status_code == 501 and r.json()["code"] == "not_implemented"
                continue
            assert r.status_code == 202
            job = c.get(f"/api/jobs/{r.json()['id']}?wait=60", headers=AUTH).json()
            assert job["status"] == "done", job
            # нечего проверять — нечего и применять
            assert job["result"]["modules"] == [] and job["result"]["applied"] is False
        finally:
            state.close()


def test_passed_checks_restart_executors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def passed(root: Path, modules: list[str], ctx: object = None) -> list[devmode.ModuleCheck]:
        return [devmode.ModuleCheck(module=m, ok=True, seconds=0.1, output="1 passed") for m in modules]

    monkeypatch.setattr(devmode, "check_modules", passed)
    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, dev=True)
    state = ServerState.open(settings)
    # изменены до проверки, во время проверки — ничего
    start = state.applied_at
    monkeypatch.setattr(devmode, "changed_modules", lambda root, since: ["engine"] if since <= start else [])
    try:
        c = TestClient(create_app(state, dev=True))
        assert c.post("/api/modules/check", json={"modules": ["no_such"]}, headers=AUTH).status_code == 422
        before = state.applied_at
        r = c.post("/api/modules/check", json={"modules": ["engine", "storage"]}, headers=AUTH)
        result = c.get(f"/api/jobs/{r.json()['id']}?wait=60", headers=AUTH).json()["result"]
        assert result["applied"] is True and state.applied_at >= before
        assert result["restart_app"] is True  # storage работает в процессе сервера
    finally:
        state.close()


def test_module_names_are_folder_names_not_paths(tmp_path: Path):
    for name in ("steps-std", "engine", "server"):
        (tmp_path / "packages" / name).mkdir(parents=True)
    asked = ["steps_std", "engine", "steps-std", "..", str(tmp_path), "x/.."]
    found, unknown = devmode.resolve_modules(tmp_path, asked)
    assert found == ["steps-std", "engine"]
    assert unknown == ["..", str(tmp_path), "x/.."]


def test_cyrillic_test_output_survives_a_non_utf8_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # консоль Windows (cp1251/cp866) у `agen serve --dev`: процесс тестов всё равно пишет в UTF-8
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    _module(tmp_path, "ru", "def test_ru():\n    print('вывод')\n    assert False, 'кириллица'\n")
    check = devmode.test_module(tmp_path, "ru")
    assert not check.ok and "кириллица" in check.output, check.output


def test_cancel_stops_running_tests(tmp_path: Path):
    _module(tmp_path, "slow", "import time\n\ndef test_slow():\n    time.sleep(60)\n")
    t0 = time.monotonic()
    with pytest.raises(AgenError) as e:
        devmode.test_module(tmp_path, "slow", cancelled=lambda: time.monotonic() - t0 > 1.5)
    assert e.value.code == ErrorCode.CANCELLED
    assert time.monotonic() - t0 < 20  # pytest завершён, а не дождались его


def _passed(root: Path, modules: list[str], ctx: object = None) -> list[devmode.ModuleCheck]:
    return [devmode.ModuleCheck(module=m, ok=True, seconds=0.1, output="1 passed") for m in modules]


def test_cancel_after_the_last_module_does_not_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, dev=True)
    state = ServerState.open(settings)

    def cancelled_at_the_end(root: Path, modules: list[str], ctx: JobContext) -> list[devmode.ModuleCheck]:
        state.jobs.cancel(ctx.job_id)  # отмена пришла, когда тесты последнего модуля уже прошли
        return _passed(root, modules)

    monkeypatch.setattr(devmode, "check_modules", cancelled_at_the_end)
    try:
        c = TestClient(create_app(state, dev=True))
        before = state.applied_at
        r = c.post("/api/modules/check", json={"modules": ["engine"]}, headers=AUTH)
        job = c.get(f"/api/jobs/{r.json()['id']}?wait=60", headers=AUTH).json()
        assert job["status"] == "cancelled", job
        assert state.applied_at == before
    finally:
        state.close()


def test_listed_modules_are_not_applied_while_others_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(devmode, "check_modules", _passed)
    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, dev=True)
    state = ServerState.open(settings)
    start = state.applied_at
    monkeypatch.setattr(devmode, "changed_modules", lambda root, since: ["engine", "render"] if since <= start else [])
    try:
        c = TestClient(create_app(state, dev=True))
        before = state.applied_at
        r = c.post("/api/modules/check", json={"modules": ["engine"]}, headers=AUTH)
        result = c.get(f"/api/jobs/{r.json()['id']}?wait=60", headers=AUTH).json()["result"]
        # render не проверен: исполнители не перезапущены, и render остаётся изменённым
        assert result["applied"] is False and "render" in result["note"]
        assert state.applied_at == before
    finally:
        state.close()


def test_code_changed_during_the_check_is_not_applied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(devmode, "check_modules", _passed)
    monkeypatch.setattr(devmode, "changed_modules", lambda root, since: ["engine"])  # и до, и во время
    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, dev=True)
    state = ServerState.open(settings)
    try:
        c = TestClient(create_app(state, dev=True))
        r = c.post("/api/modules/check", json={}, headers=AUTH)
        result = c.get(f"/api/jobs/{r.json()['id']}?wait=60", headers=AUTH).json()["result"]
        assert result["applied"] is False and "во время проверки" in result["note"]
    finally:
        state.close()
