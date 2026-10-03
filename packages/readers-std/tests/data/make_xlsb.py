"""Как сделан ``обращения.xlsb`` — маленькая синтетическая двоичная книга Excel для тестов.

Распространённой библиотеки Python, которая пишет .xlsb, нет, поэтому книга собирается
здесь вручную по спецификации MS-XLSB. Это zip с частями OOXML, как у .xlsx, только книга,
листы, общие строки и стили в нём — двоичные записи BIFF12, а не XML. Записи те, что пишет
Excel, кроме необязательных, которые читателям не нужны (вид листа, тема, свойства
документа). Пересоздать:

    uv run python packages/readers-std/tests/data/make_xlsb.py

Данные те же, что в ``обращения.xls`` (``make_xls.py``): два листа с одной шапкой, над
шапкой — пустая строка и три строки параметров отчёта, у двух обращений «Закрыто» пустое.
Числа записаны теми же видами записей, что у Excel: целые и короткие дроби — 4-байтовым
RK-числом (старшие 30 бит double), 1,1 — RK-числом в сотых, дата со временем — 8-байтовым
double. У «Создано» встроенный формат даты (номер 14, в русском Excel — ДД.ММ.ГГГГ), у
«Закрыто» — свой формат «dd.mm.yyyy hh:mm». Строки — в общей таблице строк.
"""

from __future__ import annotations

import datetime as dt
import struct
import zipfile
from pathlib import Path

OUT = Path(__file__).with_name("обращения.xlsb")
PREAMBLE: list[list[object]] = [
    [],
    ["Отчёт по обращениям"],
    ["Период", "январь 2026"],
    ["Подразделение", "Служба поддержки"],
]
HEADER = ["Номер", "Тема", "Ответов", "Часы", "Создано", "Закрыто"]
SHEETS: dict[str, list[list[object]]] = {
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

EPOCH = dt.datetime(1899, 12, 30)
"""День ноль дат Excel (система дат 1900 года)."""
XF_GENERAL, XF_DAY, XF_MOMENT = 0, 1, 2
"""Форматы ячеек из styles.bin: общий, дата (встроенный формат 14), дата со временем."""
FMT_MOMENT = 164
"""Номер своего формата числа: свои начинаются со 164."""
AUTO_COLOR = bytes([1, 0, 0, 0, 0, 0, 0, 0])
"""BrtColor «авто»."""

PART_TYPES = {
    # Так тип книги .xlsb записывает Excel (и в книге без макросов).
    "xl/workbook.bin": "application/vnd.ms-excel.sheet.binary.macroEnabled.main",
    "xl/styles.bin": "application/vnd.ms-excel.styles",
    "xl/sharedStrings.bin": "application/vnd.ms-excel.sharedStrings",
}
SHEET_TYPE = "application/vnd.ms-excel.worksheet"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _rec(rt: int, body: bytes = b"") -> bytes:
    """Запись BIFF12: тип (1–2 байта) и длина тела (1–4 байта) — по 7 бит в байте, старший
    бит значит «дальше ещё байт»; за ними тело."""
    out = bytearray([rt & 0x7F | 0x80, rt >> 7] if rt >= 0x80 else [rt])
    n = len(body)
    while n >= 0x80:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out) + body


def _wide(s: str) -> bytes:
    """XLWideString: число знаков UTF-16 и сами знаки."""
    data = s.encode("utf-16-le")
    return struct.pack("<I", len(data) // 2) + data


def _rk(value: float) -> bytes | None:
    """Число в 4 байтах RK, если Excel записал бы его так; иначе ``None`` (тогда — double)."""
    raw = struct.pack("<d", value)
    if raw[:4] == bytes(4) and not raw[4] & 0x03:
        return raw[4:]  # младшие 34 бита double — нули: хранятся старшие 30
    cents = round(value * 100)
    if cents / 100 == value and -(1 << 29) <= cents < 1 << 29:
        return struct.pack("<i", cents << 2 | 0x03)  # целое число сотых (fInt и fX100)
    return None


def _number(value: object) -> tuple[float, int]:
    """Значение ячейки как число Excel и формат ячейки."""
    if isinstance(value, dt.datetime):
        return (value - EPOCH) / dt.timedelta(days=1), XF_MOMENT
    if isinstance(value, dt.date):
        return float((value - EPOCH.date()).days), XF_DAY
    assert isinstance(value, int | float)
    return float(value), XF_GENERAL


def _sheet(rows: list[list[object]], strings: dict[str, int]) -> bytes:
    first = next(i for i, row in enumerate(rows) if row)
    width = max(len(row) for row in rows)
    # BrtBeginSheet; BrtWsDim — занятые строки и столбцы; BrtBeginSheetData
    out = [_rec(129), _rec(148, struct.pack("<IIII", first, len(rows) - 1, 0, width - 1)), _rec(145)]
    for r, row in enumerate(rows):
        if not row:
            continue  # пустую строку Excel не пишет
        # BrtRowHdr: номер строки, формат, высота (в двадцатых пункта), флаги и один
        # диапазон занятых столбцов
        out.append(_rec(0, struct.pack("<IIH3sIII", r, 0, 300, bytes(3), 1, 0, len(row) - 1)))
        for c, value in enumerate(row):
            if value is None:
                continue
            if isinstance(value, str):
                # BrtCellIsst: столбец, формат ячейки и номер в общей таблице строк
                out.append(_rec(7, struct.pack("<III", c, XF_GENERAL, strings.setdefault(value, len(strings)))))
                continue
            number, xf = _number(value)
            cell = struct.pack("<II", c, xf)
            rk = _rk(number)
            # BrtCellRk — число в 4 байтах, BrtCellReal — в 8
            out.append(_rec(2, cell + rk) if rk else _rec(5, cell + struct.pack("<d", number)))
    out += [_rec(146), _rec(130)]  # BrtEndSheetData, BrtEndSheet
    return b"".join(out)


def _workbook(names: list[str]) -> bytes:
    out = [
        _rec(131),  # BrtBeginBook
        _rec(153, struct.pack("<II", 0, 0) + _wide("")),  # BrtWbProp: даты от 1900 года
        _rec(135),  # BrtBeginBookViews
        _rec(158, struct.pack("<iiIIIIIB", 0, 0, 23040, 8880, 600, 0, 0, 0x78)),  # BrtBookView: окно книги
        _rec(136),  # BrtEndBookViews
        _rec(143),  # BrtBeginBundleShs
    ]
    for i, name in enumerate(names, start=1):
        # BrtBundleSh: видимый лист, его номер, связь с частью листа и имя
        out.append(_rec(156, struct.pack("<II", 0, i) + _wide(f"rId{i}") + _wide(name)))
    out += [_rec(144), _rec(132)]  # BrtEndBundleShs, BrtEndBook
    return b"".join(out)


def _shared_strings(strings: dict[str, int], refs: int) -> bytes:
    # BrtBeginSst: всего ссылок на строки и разных строк; BrtSSTItem: строка без оформления
    out = [_rec(159, struct.pack("<II", refs, len(strings)))]
    out += [_rec(19, b"\0" + _wide(s)) for s in strings]
    out.append(_rec(160))  # BrtEndSst
    return b"".join(out)


def _xf(parent: int, fmt: int, used: int) -> bytes:
    """BrtXF: стиль-родитель, формат числа, шрифт, заливка и рамка (первые), выравнивание
    «по нижнему краю», ячейка защищена; ``used`` — какие свойства свои, а не от родителя
    (1 — формат числа)."""
    return struct.pack("<HHHHHBBHH", parent, fmt, 0, 0, 0, 0, 0, 0x1010, used)


def _styles() -> bytes:
    fills = [
        # BrtFill: узор (нет, «серый 12,5%»), цвета узора и фона, градиента нет
        struct.pack("<I", pattern)
        + bytes([3, 64, 0, 0, 0, 0, 0, 0xFF])
        + bytes([3, 65, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF])
        + struct.pack("<I5dI", 0, 0, 0, 0, 0, 0, 0)
        for pattern in (0, 17)
    ]
    return b"".join(
        [
            _rec(278),  # BrtBeginStyleSheet
            _rec(615, struct.pack("<I", 1)),  # BrtBeginFmts
            _rec(44, struct.pack("<H", FMT_MOMENT) + _wide("dd.mm.yyyy hh:mm")),  # BrtFmt
            _rec(616),  # BrtEndFmts
            _rec(611, struct.pack("<I", 1)),  # BrtBeginFonts
            # BrtFont: 10 пт, обычный, без подчёркивания, семейство swiss, кириллица, цвет «авто»
            _rec(43, struct.pack("<HHHHBBBB", 200, 0, 400, 0, 0, 2, 204, 0) + AUTO_COLOR + b"\0" + _wide("Arial")),
            _rec(612),  # BrtEndFonts
            _rec(603, struct.pack("<I", len(fills))),  # BrtBeginFills
            *(_rec(45, fill) for fill in fills),
            _rec(604),  # BrtEndFills
            _rec(613, struct.pack("<I", 1)),  # BrtBeginBorders
            _rec(46, b"\0" + (b"\0\0" + AUTO_COLOR) * 5),  # BrtBorder: без линий
            _rec(614),  # BrtEndBorders
            _rec(626, struct.pack("<I", 1)),  # BrtBeginCellStyleXFs
            _rec(47, _xf(0xFFFF, 0, 0)),
            _rec(627),  # BrtEndCellStyleXFs
            _rec(617, struct.pack("<I", 3)),  # BrtBeginCellXFs: XF_GENERAL, XF_DAY, XF_MOMENT
            _rec(47, _xf(0, 0, 0)),
            _rec(47, _xf(0, 14, 1)),
            _rec(47, _xf(0, FMT_MOMENT, 1)),
            _rec(618),  # BrtEndCellXFs
            _rec(619, struct.pack("<I", 1)),  # BrtBeginStyles
            _rec(48, struct.pack("<IHBB", 0, 1, 0, 0xFF) + _wide("Normal")),  # BrtStyle: встроенный «Обычный»
            _rec(620),  # BrtEndStyles
            _rec(279),  # BrtEndStyleSheet
        ]
    )


def _relationships(rels: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<Relationship Id="rId{i}" Type="{kind}" Target="{target}"/>' for i, (kind, target) in enumerate(rels, start=1)
    )
    return f'{XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{items}</Relationships>'


def main() -> None:
    tables = [[*PREAMBLE, HEADER, *rows] for rows in SHEETS.values()]
    strings: dict[str, int] = {}
    sheets = {f"xl/worksheets/sheet{i}.bin": _sheet(rows, strings) for i, rows in enumerate(tables, start=1)}
    refs = sum(isinstance(v, str) for rows in tables for row in rows for v in row)
    types = {**PART_TYPES, **dict.fromkeys(sheets, SHEET_TYPE)}
    overrides = "".join(f'<Override PartName="/{name}" ContentType="{ct}"/>' for name, ct in types.items())
    parts = {
        "[Content_Types].xml": (
            f'{XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>'
        ),
        "_rels/.rels": _relationships([(f"{REL}/officeDocument", "xl/workbook.bin")]),
        "xl/workbook.bin": _workbook(list(SHEETS)),
        "xl/_rels/workbook.bin.rels": _relationships(
            [(f"{REL}/worksheet", name.removeprefix("xl/")) for name in sheets]
            + [(f"{REL}/styles", "styles.bin"), (f"{REL}/sharedStrings", "sharedStrings.bin")]
        ),
        **sheets,
        "xl/styles.bin": _styles(),
        "xl/sharedStrings.bin": _shared_strings(strings, refs),
    }
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            # Время в архиве постоянное, как у Excel: пересозданный файл не отличается без причины.
            z.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)


if __name__ == "__main__":
    main()
