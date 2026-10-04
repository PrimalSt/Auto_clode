"""Запуск сервера: ``python -m autogenerator.server`` (так его запускают оболочка и ``agen serve``).

Сервер слушает только ``127.0.0.1``; порт по умолчанию — случайный свободный. Когда сервер
готов, он печатает строку ``AGEN_SERVER_READY {"port": …, "pid": …, "url": …}``: по ней оболочка
узнаёт порт. Если не запустился — ``AGEN_SERVER_ERROR {"code": …, "message": …}`` и код выхода 3
(например, папка данных уже открыта другим сервером).

Токен оболочка передаёт переменной ``AGEN_TOKEN``; без неё сервер создаёт свой. Для CLI сервер
кладёт в папку данных ``server.json`` (порт и номер процесса) и ``cli.token`` (токен, файл
доступен только текущему пользователю) и удаляет их при остановке. С ``--parent`` сервер
останавливается сам, если пропал процесс оболочки.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from autogenerator.contracts import AgenError

from .app import create_app, openapi_schema
from .routes.system import app_version
from .state import ServerState, Settings

READY = "AGEN_SERVER_READY"
ERROR = "AGEN_SERVER_ERROR"
SERVER_FILE = "server.json"
TOKEN_FILE = "cli.token"
EXIT_START_FAILED = 3


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m autogenerator.server", description="Локальный сервер Autogenerator")
    p.add_argument("--home", help="Папка данных (по умолчанию — AGEN_HOME или папка приложения)")
    p.add_argument("--port", type=int, default=0, help="Порт на 127.0.0.1 (0 — случайный свободный)")
    p.add_argument("--parent", type=int, help="Номер процесса оболочки: без него сервер останавливается")
    p.add_argument("--origin", action="append", default=[], help="Origin окна для CORS (можно несколько)")
    p.add_argument("--dev", action="store_true", help="Режим разработчика: /docs без токена")
    p.add_argument("--no-prestart", action="store_true", help="Запускать исполнители при первом задании")
    p.add_argument("--ui", help="Папка собранного интерфейса (по умолчанию — ui рядом с пакетом сервера)")
    p.add_argument("--openapi", metavar="ФАЙЛ", help="Записать схему OpenAPI в файл и выйти (папка данных не нужна)")
    return p.parse_args(argv)


def _say(tag: str, data: dict[str, object]) -> None:
    print(f"{tag} {json.dumps(data, ensure_ascii=False)}", flush=True)


def _logging(folder: Path) -> None:
    logs = folder / "logs"
    logs.mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs / "server.log", maxBytes=5 << 20, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _write_private(path: Path, text: str) -> None:
    """Файл, доступный только текущему пользователю (в Windows папка данных и так в его профиле)."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        synchronize, wait_timeout = 0x00100000, 0x102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(handle, 0) == wait_timeout)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _watch_parent(pid: int, stop: threading.Event, on_gone: object) -> None:
    while not stop.wait(1.0):
        if not _alive(pid):
            logging.getLogger(__name__).warning("Процесс оболочки %s пропал: сервер останавливается", pid)
            on_gone()  # type: ignore[operator]
            return


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    a = _args(argv)
    if a.openapi:
        text = json.dumps(openapi_schema(), ensure_ascii=False, indent=2) + "\n"
        Path(a.openapi).write_text(text, encoding="utf-8")
        return 0
    settings = Settings(home=a.home, origins=list(a.origin), prestart=not a.no_prestart)
    if a.ui:
        settings.ui = Path(a.ui)
    if os.environ.get("AGEN_TOKEN"):
        settings.token = os.environ["AGEN_TOKEN"]
    try:
        state = ServerState.open(settings)
    except AgenError as e:
        _say(ERROR, {"code": str(e.code), "message": e.message, "hint": e.hint})
        return EXIT_START_FAILED
    folder = state.home.folder.root
    _logging(folder)
    stop = threading.Event()
    server_file, token_file = folder / SERVER_FILE, folder / TOKEN_FILE
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", a.port))
        except OSError as e:
            _say(ERROR, {"code": "port_busy", "message": f"Порт {a.port} на 127.0.0.1 занят: {e}"})
            return EXIT_START_FAILED
        port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        app = create_app(state, dev=a.dev)
        config = uvicorn.Config(app, log_config=None, access_log=False, lifespan="off", timeout_graceful_shutdown=5)
        server = uvicorn.Server(config)

        def shutdown() -> None:
            server.should_exit = True

        state.shutdown = shutdown
        if a.parent:
            threading.Thread(target=_watch_parent, args=(a.parent, stop, shutdown), daemon=True).start()
        info = {
            "port": port,
            "pid": os.getpid(),
            "url": url,
            "version": app_version(),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _write_private(token_file, settings.token)
        server_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")

        def announce() -> None:
            # строка готовности — когда uvicorn уже принимает соединения
            while not server.started and not server.should_exit and not stop.is_set():
                time.sleep(0.05)
            if server.started:
                logging.getLogger(__name__).info("Сервер готов: %s, папка данных %s", url, folder)
                _say(READY, {"port": port, "pid": os.getpid(), "url": url})

        threading.Thread(target=announce, daemon=True).start()
        server.run(sockets=[sock])
        return 0
    finally:
        stop.set()
        for f in (server_file, token_file):
            with contextlib.suppress(OSError):
                f.unlink(missing_ok=True)
        state.close()
