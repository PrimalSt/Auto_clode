"""Читатель Excel 97–2003 (.xls) на fastexcel.

Правила и код те же, что у .xlsx (``excel_reader.py``): шапка ищется по первым строкам
листа, если лист не указан — в выгрузку входят первый лист с данными и следующие листы с
такой же шапкой, все ячейки приводятся к тексту, даты Excel становятся строками
«2026-01-01 00:00:00». Свой только ``can_read``: какие файлы узнавать. Первые строки
листов даёт сам fastexcel: XML, который можно разбирать потоком, в .xls нет, да и не
нужен — книга (не больше 65 536 строк на лист) целиком читается при открытии. Проверки
на zip-бомбу нет: .xls — не zip.
"""

from __future__ import annotations

from pathlib import Path

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
