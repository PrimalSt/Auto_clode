"""Окно по адресу сервера, схемы для редактора, схема OpenAPI без папки данных."""

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
