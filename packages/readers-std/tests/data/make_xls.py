"""Как сделан ``обращения.xls`` — маленькая синтетическая выгрузка Excel 97–2003 для тестов.

Писать .xls умеет xlwt; приложению он не нужен и в зависимости не входит, поэтому файл
лежит в репозитории готовым. Пересоздать его:

    uv run --with xlwt python packages/readers-std/tests/data/make_xls.py

Два листа с одной шапкой (выгрузка, разделённая на листы), над шапкой — пустая строка и
три строки параметров отчёта. В строках — текст, целые и дробные числа, даты и даты
со временем (у двух обращений «Закрыто» пустое).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import xlwt

OUT = Path(__file__).with_name("обращения.xls")
PREAMBLE = [[], ["Отчёт по обращениям"], ["Период", "январь 2026"], ["Подразделение", "Служба поддержки"]]
HEADER = ["Номер", "Тема", "Ответов", "Часы", "Создано", "Закрыто"]
SHEETS = {
    "Часть 1": [
        [1001, "Не приходит письмо", 2, 1.5, dt.date(2026, 1, 9), dt.datetime(2026, 1, 9, 15, 30)],
        [1002, "Ошибка в счёте", 4, 3.25, dt.date(2026, 1, 12), dt.datetime(2026, 1, 14, 10, 0)],
        [1003, "Смена пароля", 1, 0.5, dt.date(2026, 1, 12), dt.datetime(2026, 1, 12, 11, 45)],
        [1004, "Не работает выгрузка", 7, 12.75, dt.date(2026, 1, 15), None],
        [1005, "Вопрос по договору", 3, 2, dt.date(2026, 1, 20), dt.datetime(2026, 1, 21, 9, 5)],
        [1006, "Доступ к отчётам", 2, 1.1, dt.date(2026, 1, 22), dt.datetime(2026, 1, 22, 17, 20)],
    ],
    "Часть 2": [
        [1007, "Не приходит письмо", 5, 4.5, dt.date(2026, 1, 26), dt.datetime(2026, 1, 28, 12, 0)],
        [1008, "Ошибка в счёте", 2, 0.75, dt.date(2026, 1, 27), dt.datetime(2026, 1, 27, 16, 40)],
        [1009, "Смена тарифа", 1, 0.25, dt.date(2026, 1, 29), None],
        [1010, "Вопрос по договору", 6, 8, dt.date(2026, 1, 31), dt.datetime(2026, 1, 31, 18, 10)],
    ],
}


def main() -> None:
    book = xlwt.Workbook(encoding="utf-8")
    day = xlwt.easyxf(num_format_str="DD.MM.YYYY")
    moment = xlwt.easyxf(num_format_str="DD.MM.YYYY HH:MM")
    for name, rows in SHEETS.items():
        sheet = book.add_sheet(name)
        for i, row in enumerate([*PREAMBLE, HEADER, *rows]):
            for j, value in enumerate(row):
                if isinstance(value, dt.datetime):
                    sheet.write(i, j, value, moment)
                elif isinstance(value, dt.date):
                    sheet.write(i, j, value, day)
                elif value is not None:
                    sheet.write(i, j, value)
    book.save(str(OUT))


if __name__ == "__main__":
    main()
