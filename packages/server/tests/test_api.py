"""API сервера на примере examples/sales: настоящие процессы-исполнители, запросы через TestClient."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from autogenerator.server import ServerState, Settings, create_app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "sales"
TEMPLATE = ROOT / "examples" / "templates" / "synthetic.pptx"
JAN, FEB, MAR = (EXAMPLE / "data" / "sales" / f"Продажи_2026-0{m}.csv" for m in (1, 2, 3))
PLAN = EXAMPLE / "data" / "plan" / "План_2026-Q1.xlsx"
TOKEN = "test-token"
WAIT = {"wait": 120}


@pytest.fixture(scope="module")
def state(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ServerState]:
    home = tmp_path_factory.mktemp("server") / "home"
    st = ServerState.open(Settings(home=home, token=TOKEN, hosts=["testserver"]))
    yield st
    st.close()


@pytest.fixture(scope="module")
def api(state: ServerState) -> Iterator[TestClient]:
    with TestClient(create_app(state), headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


def done(job: dict[str, Any]) -> Any:
    assert job["status"] == "done", job.get("error")
    return job["result"]


def failed(job: dict[str, Any], code: str) -> dict[str, Any]:
    assert job["status"] == "failed", job
    assert job["error"]["code"] == code, job["error"]
    return job["error"]


def test_system_and_errors(api: TestClient, state: ServerState):
    assert TestClient(create_app(state)).get("/api/system").status_code == 401
    info = api.get("/api/system").json()
    assert info["home"] == str(state.home.folder.root) and {e["name"] for e in info["executors"]} == {"main", "light"}
    r = api.get("/api/sources/nope")
    assert r.status_code == 404 and r.json()["code"] == "not_found" and "nope" in r.json()["message"]
    r = api.post("/api/sources/x/uploads", json={"paths": []})
    assert r.status_code == 422 and r.json()["code"] == "spec_invalid"
    mods = api.get("/api/modules").json()
    assert mods["error"] is None and any(p["name"] == "dedupe" for p in mods["plugins"]["plugins"])


def test_sources_uploads_and_mapping(api: TestClient, tmp_path: Path):
    job = api.post("/api/sources/draft", params=WAIT, json={"path": str(JAN), "id": "draft_only"}).json()
    draft = done(job)
    assert draft["source"]["id"] == "draft_only" and draft["snapshot"]["columns"]
    assert api.get("/api/sources/draft_only").status_code == 404  # черновик не сохраняется

    src = {
        "id": "s",
        "name": "S",
        "period_column": "date",
        "columns": [
            {"id": "date", "name": "Дата", "dtype": "date"},
            {"id": "region", "name": "Регион"},
            {"id": "amount", "name": "Сумма", "dtype": "float"},
        ],
    }
    assert api.post("/api/sources", json={"spec": src}).status_code == 201
    a = tmp_path / "a.csv"
    a.write_text("Дата;Регион;Сумма\n01.01.2026;Москва;10\n", encoding="utf-8")
    up = done(api.post("/api/sources/s/uploads", params=WAIT, json={"paths": [str(a)]}).json())
    assert up["record"]["rows"] == 1 and up["remembered"] == {}
    failed(api.post("/api/sources/s/uploads", params=WAIT, json={"paths": [str(a)]}).json(), "already_exists")

    # Переименованный столбец: загрузка ждёт сопоставления, окно отправляет её снова с решением.
    b = tmp_path / "b.csv"
    b.write_text("Дата;Регион;Сумма заказа\n02.02.2026;Казань;20\n", encoding="utf-8")
    err = failed(api.post("/api/sources/s/uploads", params=WAIT, json={"paths": [str(b)]}).json(), "schema_review")
    rec = err["details"]["files"]["b.csv"]
    assert rec["proposed"] == {"Сумма заказа": "amount"}
    up = done(api.post("/api/sources/s/uploads", params=WAIT, json={"paths": [str(b)], "accept_mapping": True}).json())
    assert up["remembered"] == {"Сумма заказа": "amount"}
    assert "Сумма заказа" in api.get("/api/sources/s").json()["spec"]["columns"][2]["aliases"]
    assert [v["number"] for v in api.get("/api/sources/s/versions").json()] == [1, 2]

    hist = api.get("/api/sources/s/history").json()
    assert len(hist["uploads"]) == 2 and hist["coverage"]["spans"] and hist["disk_usage"] > 0
    first = hist["uploads"][0]["id"]
    excluded = api.patch(f"/api/uploads/{first}", json={"status": "excluded"}).json()
    assert excluded["status"] == "excluded"
    assert api.delete(f"/api/uploads/{first}").status_code == 204
    assert len(api.get("/api/sources/s/uploads").json()) == 1


def test_themes_scenarios_runs_and_preview(api: TestClient, state: ServerState, tmp_path: Path):
    assert len(api.post("/api/sources/import", json={"path": str(EXAMPLE / "sources.yaml")}).json()) == 2
    for source, f in (("sales_crm", JAN), ("sales_crm", FEB), ("sales_plan", PLAN)):
        done(api.post(f"/api/sources/{source}/uploads", params=WAIT, json={"paths": [str(f)]}).json())

    imp = done(api.post("/api/themes", params=WAIT, json={"path": str(TEMPLATE)}).json())
    assert imp["record"]["id"] == "synthetic" and not imp["skipped"]
    again = done(api.post("/api/themes", params=WAIT, json={"path": str(TEMPLATE)}).json())
    assert again["skipped"] and again["matched"] == 1
    assert api.get("/api/themes/synthetic").json()["current"]["manifest"]["layouts"]

    text = (EXAMPLE / "scenario.yaml").read_text(encoding="utf-8")
    # окно сохраняет сценарий только с id шаблона из папки данных: путь к файлу не принимается
    r = api.post("/api/scenarios", json={"text": text, "id": "report"})
    assert r.status_code == 404 and "synthetic.pptx" in r.json()["message"]
    saved = api.post("/api/scenarios", json={"text": text, "id": "report", "theme": "synthetic"}).json()
    assert saved["record"]["current"]["theme_id"] == "synthetic"
    assert not [i for i in saved["issues"] if i["level"] == "error"]
    assert "theme: synthetic" in api.get("/api/scenarios/report/yaml").text
    assert api.post("/api/scenarios", json={"text": text, "id": "report", "theme": "synthetic"}).status_code == 409
    assert api.get("/api/scenarios/report/validate").json() is not None

    run = done(api.post("/api/scenarios/report/runs", params=WAIT, json={"output": str(tmp_path / "out")}).json())
    assert run["status"] == "ok" and run["id"] == "report-001" and run["period"]["start"] == "2026-02-01"
    assert run["trigger"] == "app"
    pptx = api.get(f"/api/runs/{run['id']}/output")
    assert pptx.status_code == 200 and pptx.content[:2] == b"PK"
    assert [r["id"] for r in api.get("/api/runs", params={"scenario": "report"}).json()] == ["report-001"]
    rerun = done(api.post(f"/api/runs/{run['id']}/rerun", params=WAIT, json={}).json())
    assert rerun["id"] == "report-002" and rerun["period"]["start"] == "2026-02-01"

    # Превью черновика: без сохранения, в исполнителе превью.
    spec = api.get("/api/scenarios/report").json()["current"]["spec"]
    assert api.post("/api/preview/validate", json={"spec": spec}).json() == []
    bad = api.post("/api/preview/validate", json={"text": "name: [1"}).json()
    assert bad and bad[0]["level"] == "error"
    node = done(api.post("/api/preview/node", params=WAIT, json={"spec": spec, "target": "dataset:plan_fact"}).json())
    assert node["rows"] and node["columns"]
    slide = done(api.post("/api/preview/slide", params=WAIT, json={"spec": spec, "slide": 1, "image": False}).json())
    assert slide["files"]["pptx"].endswith("/slide.pptx") and "png" not in slide["files"]
    assert api.get(slide["files"]["pptx"]).content[:2] == b"PK"
    assert api.get(f"/api/preview/files/{slide['id']}/other.txt").status_code == 422

    # Задания видны списком и по id; превью шли в очереди light.
    jobs = api.get("/api/jobs").json()
    assert {j["lane"] for j in jobs if j["kind"].startswith("preview")} == {"light"}
    assert api.get(f"/api/jobs/{jobs[0]['id']}").json()["result"] is not None

    # Резервная копия и возврат к ней.
    b = api.post("/api/system/backup", json={"label": "api"}).json()
    assert api.delete("/api/runs/report-002").status_code == 204
    restored = api.post("/api/system/restore", json={"path": b["name"]}).json()
    assert Path(restored["safety"]).is_file()
    assert len(api.get("/api/runs").json()) == 2
    assert b["name"] in {x["name"] for x in api.get("/api/system/backups").json()}


def test_events_carry_jobs_and_changes(state: ServerState):
    sub = state.bus.subscribe(after=0)  # с начала буфера: всё, что было в этом модуле
    kinds: set[str] = set()
    changed: set[str] = set()
    while (e := sub.get(timeout=0.1)) is not None:
        kinds.add(e.kind)
        if e.kind == "changed":
            changed.add(e.data["what"])
        if e.kind == "job":
            assert "result" not in e.data  # итог — только в GET /api/jobs/{id}
    sub.close()
    assert kinds == {"job", "changed"}
    assert {"sources", "uploads", "themes", "scenarios", "runs", "all"} <= changed
