"""Читатель двоичных книг Excel (.xlsb) на fastexcel.

Правила и код те же, что у .xlsx (``excel_reader.py``): шапка ищется по первым строкам
листа, если лист не указан — в выгрузку входят первый лист с данными и следующие листы с
такой же шапкой, все ячейки приводятся к тексту, даты Excel становятся строками
«2026-01-01 00:00:00». Свой только ``can_read``: какие файлы узнавать.

.xlsb — такой же zip, как .xlsx, и перед чтением так же проверяется на zip-бомбу, но книга,
листы и общие строки в нём — двоичные записи (BIFF12), а не XML. Поэтому первые строки
листов даёт сам fastexcel, как у .xls, и лист ради них читается целиком (до 1 048 576 строк).
"""

from __future__ import annotations

from pathlib import Path

from .excel_reader import ExcelReader, book_format


class XlsbReader(ExcelReader):
    name = "xlsb"
    title = "Двоичная книга Excel"
    formats = ("xlsb",)

    def can_read(self, path: Path) -> bool:
        # По содержимому (часть xl/workbook.bin), а не по расширению: .xlsb, названную .xlsx,
        # читатель берёт, а .xlsx, названную .xlsb, — нет.
        return book_format(path) == "xlsb"
