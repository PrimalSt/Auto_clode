"""Приложение FastAPI: разделы API, токен, CORS, ошибки."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import errors
from .auth import OPEN_PATHS, TokenMiddleware
from .routes import ROUTERS
from .routes.system import app_version
from .state import ServerState

UI_DIR = Path(__file__).parent / "ui"
"""Сюда собирается интерфейс (``npm run build`` в ``frontend``)."""


def _app(dev: bool) -> FastAPI:
    app = FastAPI(
        title="Autogenerator",
        version=app_version(),
        summary="Локальный сервер приложения: источники, загрузки, сценарии, шаблоны, запуски",
        docs_url="/docs" if dev else None,
        redoc_url=None,
    )
    errors.install(app)
    for router in ROUTERS:
        app.include_router(router)
    return app


def openapi_schema() -> dict[str, Any]:
    """Схема OpenAPI без папки данных: из неё генерируются типы TypeScript интерфейса."""
    return _app(dev=False).openapi()


def create_app(state: ServerState, *, dev: bool = False) -> FastAPI:
    """Приложение поверх открытого состояния. ``dev`` — режим разработчика: страница ``/docs``.
    Если интерфейс собран (папка ``ui``), он открывается по адресу сервера."""
    app = _app(dev)
    app.state.agen = state
    ui = state.settings.ui or UI_DIR
    if (ui / "index.html").is_file():
        app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
    app.add_middleware(TokenMiddleware, token=state.settings.token, hosts=state.settings.hosts, open_paths=OPEN_PATHS)
    if state.settings.origins:
        # снаружи токена: ответ 401 тоже с заголовками CORS, и окно видит ошибку, а не «CORS»
        app.add_middleware(
            CORSMiddleware,
            allow_origins=state.settings.origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Content-Disposition"],
        )
    return app
