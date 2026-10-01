"""Чтение выгрузки: выбор читателя из реестра, вывод и приведение типов, учёт ошибок
приведения, профиль столбцов, запись Parquet по месяцам столбца периода (ARCHITECTURE.md,
раздел 6.1)."""

from .casting import CastColumn, cast_expr, cast_frame, infer_dtype
from .profile import profile_frame, profile_upload
from .reading import choose_reader, header_snapshot, inspect_file, read_options
from .writing import read_upload_table, write_upload

__all__ = [
    "CastColumn",
    "cast_expr",
    "cast_frame",
    "choose_reader",
    "header_snapshot",
    "infer_dtype",
    "inspect_file",
    "profile_frame",
    "profile_upload",
    "read_options",
    "read_upload_table",
    "write_upload",
]
