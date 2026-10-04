"""Доступ к серверу: токен и заголовок Host."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autogenerator.server.auth import TokenMiddleware

TOKEN = "secret-token"


def client(hosts: list[str] | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/thing")
    def get_thing() -> dict[str, int]:
        return {"n": 1}

    @app.post("/api/thing")
    def post_thing() -> dict[str, int]:
        return {"n": 2}

    app.add_middleware(TokenMiddleware, token=TOKEN, hosts=hosts or ["testserver"])
    return TestClient(app)


def test_token_is_required():
    c = client()
    assert c.get("/api/health").status_code == 200
    r = c.get("/api/thing")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert c.get("/api/thing", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/thing", headers={"Authorization": f"Bearer {TOKEN}"}).json() == {"n": 1}
    assert c.post("/api/thing", headers={"X-Agen-Token": TOKEN}).json() == {"n": 2}


def test_query_token_only_for_get():
    # параметр запроса — для EventSource и <img src>: только чтение
    c = client()
    assert c.get(f"/api/thing?token={TOKEN}").status_code == 200
    assert c.post(f"/api/thing?token={TOKEN}").status_code == 401


def test_foreign_host_is_rejected():
    c = client(hosts=["127.0.0.1", "localhost"])
    r = c.get("/api/health", headers={"Host": "evil.example:8000"})
    assert r.status_code == 421
    ok = c.get("/api/thing", headers={"Host": "127.0.0.1:5000", "Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200
    assert c.get("/api/health", headers={"Host": "localhost"}).status_code == 200


def test_cors_preflight_passes_without_token():
    c = client()
    r = c.options("/api/thing", headers={"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET"})
    assert r.status_code != 401
