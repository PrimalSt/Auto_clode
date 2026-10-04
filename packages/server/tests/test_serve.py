"""Сервер процессом, как его запускает оболочка: строка готовности, файлы для CLI, поток
событий по сети, остановка по запросу и когда пропала оболочка."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO

import httpx

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "serve-test-token"


def start(home: Path, *extra: str) -> subprocess.Popen[str]:
    env = {**os.environ, "AGEN_TOKEN": TOKEN, "PYTHONUTF8": "1"}
    cmd = [sys.executable, "-m", "autogenerator.server", "--home", str(home), "--no-prestart", *extra]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)


def first_line(stream: IO[str], timeout: float = 60) -> str:
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(stream.readline()), daemon=True)
    t.start()
    t.join(timeout)
    assert out, "сервер не напечатал строку готовности"
    return out[0]


def ready(proc: subprocess.Popen[str]) -> dict[str, object]:
    assert proc.stdout is not None
    tag, _, rest = first_line(proc.stdout).partition(" ")
    assert tag == "AGEN_SERVER_READY", (tag, rest, proc.stderr.read() if proc.poll() is not None else "")
    return json.loads(rest)


def wait_exit(proc: subprocess.Popen[str], timeout: float = 30) -> int:
    try:
        return proc.wait(timeout)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_server_process(tmp_path: Path):
    home = tmp_path / "home"
    proc = start(home)
    try:
        info = ready(proc)
        url = str(info["url"])
        assert url.startswith("http://127.0.0.1:")
        server = json.loads((home / "server.json").read_text(encoding="utf-8"))
        assert server["port"] == info["port"] and server["pid"] == proc.pid
        assert (home / "cli.token").read_text(encoding="utf-8") == TOKEN
        if sys.platform != "win32":
            assert (home / "cli.token").stat().st_mode & 0o077 == 0
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert httpx.get(f"{url}/api/health").json()["ok"] is True
        assert httpx.get(f"{url}/api/system").status_code == 401
        assert httpx.get(f"{url}/api/system", headers=auth).json()["pid"] == proc.pid

        # Второй сервер на ту же папку данных не запускается.
        second = start(home)
        assert second.stdout is not None
        tag, _, rest = first_line(second.stdout).partition(" ")
        assert tag == "AGEN_SERVER_ERROR" and json.loads(rest)["code"] == "data_folder_locked"
        assert wait_exit(second) == 3

        # Поток событий: токен параметром (EventSource), событие об изменении источников.
        events: list[str] = []
        with httpx.stream("GET", f"{url}/api/events", params={"token": TOKEN}, timeout=30) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            sources = ROOT / "examples" / "sales" / "sources.yaml"
            assert httpx.post(f"{url}/api/sources/import", headers=auth, json={"path": str(sources)}).is_success
            for line in r.iter_lines():
                events.append(line)
                if line.startswith("data:") and '"sources"' in line:
                    break
        assert "event: changed" in events and any(e.startswith("id: ") for e in events)

        # Исполнитель запускается из процесса сервера и делает работу.
        jan = ROOT / "examples" / "sales" / "data" / "sales" / "Продажи_2026-01.csv"
        body = {"path": str(jan), "id": "jan"}
        job = httpx.post(f"{url}/api/sources/draft?wait=120", headers=auth, json=body, timeout=130).json()
        assert job["status"] == "done", job
        executors = httpx.get(f"{url}/api/system", headers=auth).json()["executors"]
        assert {e["name"]: e["alive"] for e in executors}["main"] is True

        assert httpx.post(f"{url}/api/system/shutdown", headers=auth).status_code == 202
        assert wait_exit(proc) == 0
        assert not (home / "server.json").exists() and not (home / "cli.token").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_server_stops_when_shell_is_gone(tmp_path: Path):
    shell = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    proc = start(tmp_path / "home", "--parent", str(shell.pid))
    try:
        ready(proc)
        shell.kill()
        shell.wait()
        started = time.monotonic()
        assert wait_exit(proc) == 0
        assert time.monotonic() - started < 15
    finally:
        for p in (shell, proc):
            if p.poll() is None:
                p.kill()


def test_busy_port_is_reported(tmp_path: Path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        proc = start(tmp_path / "home", "--port", str(taken.getsockname()[1]))
        assert proc.stdout is not None
        tag, _, rest = first_line(proc.stdout).partition(" ")
        assert tag == "AGEN_SERVER_ERROR" and json.loads(rest)["code"] == "port_busy"
        assert wait_exit(proc) == 3
