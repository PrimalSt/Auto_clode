"""Окно по адресу сервера, схемы для редактора, схема OpenAPI без папки данных и только в режиме
разработчика."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from autogenerator.server import ServerState, Settings, create_app, main

AUTH = {"Authorization": "Bearer t"}


def test_ui_is_served_without_token_and_schemas_with_it(tmp_path: Path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>Autogenerator</title>", encoding="utf-8")
    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, ui=ui)
    state = ServerState.open(settings)
    try:
        c = TestClient(create_app(state))
        assert "Autogenerator" in c.get("/").text
        assert c.get("/api/schemas/scenario").status_code == 401
        schema = c.get("/api/schemas/scenario", headers=AUTH).json()
        assert "inputs" in schema["properties"]
        assert c.get("/api/schemas/source", headers=AUTH).json()["title"] == "SourceSpec"
        assert c.get("/api/schemas/other", headers=AUTH).status_code == 422
    finally:
        state.close()


def test_openapi_export_needs_no_data_folder(tmp_path: Path):
    out = tmp_path / "openapi.json"
    assert main(["--openapi", str(out)]) == 0
    paths = json.loads(out.read_text(encoding="utf-8"))["paths"]
    assert "/api/sources/{source_id}/uploads" in paths and "/api/events" in paths


def test_api_schema_pages_only_in_developer_mode(tmp_path: Path):
    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, ui=tmp_path / "нет")
    state = ServerState.open(settings)
    try:
        assert TestClient(create_app(state)).get("/openapi.json").status_code == 404
        assert TestClient(create_app(state)).get("/docs").status_code == 404
        dev = TestClient(create_app(state, dev=True))
        assert "/api/events" in dev.get("/openapi.json").json()["paths"]
        assert dev.get("/docs").status_code == 200
    finally:
        state.close()


def test_manifest_is_forgotten_on_restart_and_errors_are_remembered(tmp_path: Path, monkeypatch):
    from autogenerator.contracts import AgenError, ErrorCode

    settings = Settings(home=tmp_path / "home", token="t", hosts=["testserver"], prestart=False, ui=tmp_path / "нет")
    state = ServerState.open(settings)
    try:
        c = TestClient(create_app(state), headers=AUTH)
        assert c.get("/api/modules").json()["plugins"]["plugins"]
        calls = []

        def broken(*_a, **_k):
            calls.append(1)
            raise AgenError(ErrorCode.WORKER_FAILED, "исполнитель не запустился")

        monkeypatch.setattr(state.home.worker, "plugin_manifest", broken)
        out = c.post("/api/modules/restart").json()
        assert out["plugins"] is None and out["error"]["message"] == "исполнитель не запустился"
        assert c.get("/api/modules").json()["plugins"] is None
        assert "inputs" in c.get("/api/schemas/scenario").json()["properties"]
        assert len(calls) == 1  # ошибка запомнилась: исполнитель не запускается на каждый запрос
    finally:
        state.close()
