"""Чтение выгрузки: выбор читателя из реестра, вывод и приведение типов, учёт ошибок
приведения, запись Parquet по месяцам столбца периода (ARCHITECTURE.md, раздел 6.1)."""

from .casting import cast_expr, infer_dtype
from .reading import choose_reader, inspect_file
from .writing import read_upload_table, write_upload

__all__ = [
    "cast_expr",
    "choose_reader",
    "infer_dtype",
    "inspect_file",
    "read_upload_table",
    "write_upload",
]
