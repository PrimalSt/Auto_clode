"""Ошибки в ответах API: ``{"code", "message", "hint", "details"}`` с HTTP-статусом по коду.

Код — из ``ErrorCode``: по нему окно решает, что показать (например, экран сопоставления при
``schema_review``), а текст написан для человека, по-русски.
"""

from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from autogenerator.contracts import AgenError, ErrorCode

STATUS = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.FILE_NOT_FOUND: 404,
    ErrorCode.ALREADY_EXISTS: 409,
    ErrorCode.IN_USE: 409,
    ErrorCode.SOURCE_CHANGE: 409,
    ErrorCode.OVERLAP_CHOICE: 409,
    ErrorCode.SCHEMA_REVIEW: 409,
    ErrorCode.SCHEMA_BLOCKED: 409,
    ErrorCode.CAST_REVIEW: 409,
    ErrorCode.OUTPUT_BUSY: 409,
    ErrorCode.DATA_FOLDER_LOCKED: 423,
    ErrorCode.CANCELLED: 409,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.WORKER_FAILED: 502,
    ErrorCode.DISK_SPACE: 507,
    ErrorCode.NOT_IMPLEMENTED: 501,
}
"""HTTP-статус по коду ошибки; остальные коды — 422 (запрос не выполнить с такими данными)."""


def error_body(e: AgenError) -> dict[str, object]:
    return {"code": str(e.code), "message": e.message, "hint": e.hint, "details": jsonable_encoder(e.details)}


def install(app: FastAPI) -> None:
    @app.exception_handler(AgenError)
    async def agen_error(_: Request, e: AgenError) -> JSONResponse:
        return JSONResponse(error_body(e), status_code=STATUS.get(e.code, 422))

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, e: RequestValidationError) -> JSONResponse:
        problems = [
            f"{'.'.join(str(x) for x in err.get('loc', ()) if x != 'body')}: {err.get('msg', '')}" for err in e.errors()
        ]
        return JSONResponse(
            {
                "code": str(ErrorCode.SPEC_INVALID),
                "message": "Запрос не прошёл проверку: " + "; ".join(problems),
                "hint": None,
                "details": {"errors": jsonable_encoder(e.errors())},
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def internal(_: Request, e: Exception) -> JSONResponse:
        return JSONResponse(
            {
                "code": "internal",
                "message": f"Внутренняя ошибка сервера: {type(e).__name__}: {e}",
                "hint": "Подробности — в журнале сервера (папка logs).",
                "details": {"traceback": "".join(traceback.format_exception(e))},
            },
            status_code=500,
        )
