"""Приложение FastAPI: разделы API, токен, CORS, ошибки."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

from autogenerator.contracts import PreviewResult, ReconcileResult, RunRecord

from . import errors
from .auth import OPEN_PATHS, TokenMiddleware
from .models import SlidePreviewOut, SourceDraftOut, ThemeImportOut, UploadOut
from .routes import ROUTERS
from .routes.system import app_version
from .state import ServerState

UI_DIR = Path(__file__).parent / "ui"
"""Сюда собирается интерфейс (``npm run build`` в ``frontend``)."""


JOB_RESULTS: list[type[BaseModel]] = [
    SourceDraftOut,
    UploadOut,
    ReconcileResult,
    ThemeImportOut,
    RunRecord,
    PreviewResult,
    SlidePreviewOut,
]
"""Итоги заданий (``JobInfo.result``) и сверка из ошибки ``schema_review``: в схеме OpenAPI их
нет среди ответов, а интерфейсу нужны их типы."""


_SCHEMA_LOCK = threading.Lock()


@contextmanager
def _output_defaults_required() -> Iterator[None]:
    """В ответах поля со значением по умолчанию всегда есть, поэтому в схеме ответов они
    обязательные (как с ``json_schema_serialization_defaults_required`` у каждой модели), а в
    схеме запросов — нет. FastAPI тогда делит такие модели на ``…-Input`` и ``…-Output``, и
    типы интерфейса не помечают поля ответа как «может не быть»."""
    original = GenerateJsonSchema.field_is_required

    def field_is_required(self: GenerateJsonSchema, field: Any, total: bool) -> bool:
        if self.mode == "serialization":
            return not field.get("serialization_exclude")
        return original(self, field, total)

    with _SCHEMA_LOCK:
        GenerateJsonSchema.field_is_required = field_is_required  # type: ignore[method-assign]
        try:
            yield
        finally:
            GenerateJsonSchema.field_is_required = original  # type: ignore[method-assign]


def _with_job_results(app: FastAPI) -> None:
    def schema() -> dict[str, Any]:
        if app.openapi_schema is None:
            with _output_defaults_required():
                out = get_openapi(title=app.title, version=app.version, summary=app.summary, routes=app.routes)
                _, defs = models_json_schema(
                    [(m, "serialization") for m in JOB_RESULTS], ref_template="#/components/schemas/{model}"
                )
            known = out.setdefault("components", {}).setdefault("schemas", {})
            for name, d in defs.get("$defs", {}).items():
                known.setdefault(name, d)
            app.openapi_schema = out
        return app.openapi_schema

    app.openapi = schema  # type: ignore[method-assign]


def _app(dev: bool) -> FastAPI:
    app = FastAPI(
        title="Autogenerator",
        version=app_version(),
        summary="Локальный сервер приложения: источники, загрузки, сценарии, шаблоны, запуски",
        docs_url="/docs" if dev else None,
        openapi_url="/openapi.json" if dev else None,  # схема для типов окна — `--openapi ФАЙЛ`
        redoc_url=None,
    )
    errors.install(app)
    for router in ROUTERS:
        app.include_router(router)
    _with_job_results(app)
    return app


def openapi_schema() -> dict[str, Any]:
    """Схема OpenAPI без папки данных: из неё генерируются типы TypeScript интерфейса."""
    return _app(dev=False).openapi()


def has_ui(state: ServerState) -> bool:
    """Собран ли интерфейс: тогда окно открывается по адресу сервера."""
    return ((state.settings.ui or UI_DIR) / "index.html").is_file()


def create_app(state: ServerState, *, dev: bool = False) -> FastAPI:
    """Приложение поверх открытого состояния. ``dev`` — режим разработчика: страницы ``/docs`` и
    ``/openapi.json``.
    Если интерфейс собран (папка ``ui``), он открывается по адресу сервера."""
    app = _app(dev)
    app.state.agen = state
    if has_ui(state):
        app.mount("/", StaticFiles(directory=state.settings.ui or UI_DIR, html=True), name="ui")
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
