"""Коды ошибок и общее исключение приложения.

Модули бросают ``AgenError`` с кодом из ``ErrorCode``: по коду интерфейс и CLI решают,
как показать ошибку, а текст ``message`` пишется для человека, по-русски.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # Спецификации
    SPEC_INVALID = "spec_invalid"
    SPEC_REFERENCE = "spec_reference"
    # Плагины
    PLUGIN_NOT_FOUND = "plugin_not_found"
    PLUGIN_BROKEN = "plugin_broken"
    PLUGIN_PARAMS = "plugin_params"
    # Чтение и структура выгрузки
    FILE_NOT_FOUND = "file_not_found"
    FILE_FORMAT = "file_format"
    FILE_ENCODING = "file_encoding"
    SCHEMA_BLOCKED = "schema_blocked"
    CAST_REVIEW = "cast_review"
    CANCELLED = "cancelled"
    # История и периоды
    HISTORY_EMPTY = "history_empty"
    PERIOD_INVALID = "period_invalid"
    OVERLAP_CHOICE = "overlap_choice"
    # Папка данных, источники и загрузки
    DATA_FOLDER = "data_folder"
    DATA_FOLDER_LOCKED = "data_folder_locked"
    DISK_SPACE = "disk_space"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    SOURCE_CHANGE = "source_change"
    # Вычисления
    EXPRESSION = "expression"
    NODE_FAILED = "node_failed"
    USER_CODE = "user_code"
    TIMEOUT = "timeout"
    # Оформление и сборка
    THEME_INVALID = "theme_invalid"
    LAYOUT_MISSING = "layout_missing"
    RENDER_FAILED = "render_failed"
    OUTPUT_BUSY = "output_busy"
    NOT_IMPLEMENTED = "not_implemented"


class AgenError(Exception):
    """Ошибка, которую можно показать пользователю."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.hint = hint

    def __str__(self) -> str:
        return self.message if not self.hint else f"{self.message}\n{self.hint}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }
