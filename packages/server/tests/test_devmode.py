"""Режим разработчика: изменённые модули проверяются тестами, потом применяются."""

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
