"""Читатель Excel 97–2003 (.xls) на fastexcel.

Правила те же, что у .xlsx (``excel_reader.py``): шапка ищется по первым строкам листа,
если лист не указан — в выгрузку входят первый лист с данными и следующие листы с такой
же шапкой, все ячейки приводятся к тексту, даты Excel становятся строками
«2026-01-01 00:00:00». Отличается только доступ к книге. Первые строки листов даёт сам
fastexcel: XML, который можно разбирать потоком, в .xls нет, да и не нужен — книга (не
больше 65 536 строк на лист) целиком читается при открытии. Проверки на zip-бомбу нет:
.xls — не zip.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa

from autogenerator.contracts import AgenError, ErrorCode

from .excel_reader import ExcelReader

OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class XlsReader(ExcelReader):
    name = "xls"
    title = "Excel 97–2003"
    formats = ("xls",)

    def can_read(self, path: Path) -> bool:
        with path.open("rb") as f:
            if f.read(8) != OLE2_SIGNATURE:
                return False
        # Такая же подпись у документов Word, писем Outlook и книг с паролем: книга ли это,
        # узнаётся, только если fastexcel её откроет.
        try:
            return bool(self._open(path).sheet_names)
        except Exception:
            return False

    def _check_file(self, path: Path) -> None:
        """Не zip: проверять на zip-бомбу нечего."""

    def _sheet_names(self, path: Path) -> list[str]:
        try:
            return self._open(path).sheet_names
        except Exception as e:
            raise AgenError(ErrorCode.FILE_FORMAT, f"Не удалось открыть книгу {path.name}: {e}") from e

    def _head_rows(self, path: Path, sheet: str, n_rows: int) -> list[list[str | None]]:
        try:
            batch = self._open(path).load_sheet(
                sheet, header_row=None, skip_rows=0, n_rows=n_rows, dtypes="string", eager=True
            )
        except Exception as e:
            raise AgenError(ErrorCode.FILE_FORMAT, f"Не удалось прочитать лист «{sheet}» файла {path.name}: {e}") from e
        assert isinstance(batch, pa.RecordBatch)
        return [list(row) for row in zip(*(c.to_pylist() for c in batch.columns), strict=True)]
