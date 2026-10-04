"""Доступ к серверу (ARCHITECTURE.md, раздел 12): только с этого компьютера и только с токеном.

Токен идёт заголовком ``Authorization: Bearer …`` (или ``X-Agen-Token``). Запросам ``GET``
его можно передать параметром ``?token=``: так окно открывает поток событий (браузерный
``EventSource`` не умеет заголовки) и показывает картинки слайдов (``<img src>``). Заголовок
``Host`` должен быть ``127.0.0.1`` или ``localhost``: страница чужого сайта не достучится до
сервера, даже если подменит DNS. Без токена отвечают только ``/api/health`` и всё вне
``/api/`` — файлы самого окна (данных в них нет; токен окно получает от оболочки).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

OPEN_PATHS = frozenset({"/api/health"})


class TokenMiddleware:
    def __init__(self, app: ASGIApp, token: str, hosts: Iterable[str], open_paths: Iterable[str] = OPEN_PATHS):
        self.app = app
        self.token = token.encode()
        self.hosts = {h.lower() for h in hosts}
        self.open_paths = frozenset(open_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # потоков websocket у сервера нет
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if "*" not in self.hosts and _host(headers.get("host", "")) not in self.hosts:
            await _deny(send, 421, "wrong_host", "Сервер принимает запросы только на 127.0.0.1")
            return
        path = scope["path"]
        if scope.get("method") == "OPTIONS" or path in self.open_paths or not path.startswith("/api/"):
            await self.app(scope, receive, send)  # предварительный запрос CORS идёт без токена
            return
        if not self._allowed(scope, headers):
            await _deny(send, 401, "unauthorized", "Нужен токен приложения")
            return
        await self.app(scope, receive, send)

    def _allowed(self, scope: Scope, headers: dict[str, str]) -> bool:
        given: list[str] = []
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            given.append(auth[7:].strip())
        if "x-agen-token" in headers:
            given.append(headers["x-agen-token"].strip())
        if scope.get("method") == "GET":
            qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            given += qs.get("token", [])
        return any(secrets.compare_digest(t.encode(), self.token) for t in given)


def _host(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if ":" in value else value


async def _deny(send: Send, status: int, code: str, message: str) -> None:
    body: Any = json.dumps({"code": code, "message": message}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", b"%d" % len(body))],
        }
    )
    await send({"type": "http.response.body", "body": body})
