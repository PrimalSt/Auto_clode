from pathlib import Path

import pytest
import xlsxwriter

from autogenerator.contracts import AgenError, ReadOptions
from autogenerator.readers_std.csv_reader import CsvReader, detect_delimiter, detect_encoding
from autogenerator.readers_std.excel_reader import ExcelReader
from autogenerator.readers_std.names import dedupe_names
from autogenerator.readers_std.xls_reader import XlsReader

DATA = Path(__file__).parent / "data"
XLS = DATA / "обращения.xls"
"""Синтетическая выгрузка Excel 97–2003; как она сделана — data/make_xls.py."""


def test_detect_encoding():
    assert detect_encoding("Сумма".encode("cp1251")) == "cp1251"
    assert detect_encoding("Сумма".encode()) == "utf-8"
    assert detect_encoding("﻿Сумма".encode()) == "utf-8-sig"
    # Мегабайт мог оборваться посреди двухбайтового символа — это всё ещё UTF-8.
    assert detect_encoding("ая".encode()[:-1]) == "utf-8"


def test_detect_delimiter_ignores_commas_in_decimals_and_names():
    sample = "Дата;Сумма, руб.\n01.02.2026;12 854,00\n02.02.2026;1,5\n"
    assert detect_delimiter(sample) == ";"
    assert detect_delimiter("a,b,c\n1,2,3\n4,5,6\n") == ","
    assert detect_delimiter("a\tb\n1\t2\n") == "\t"


def test_dedupe_names():
    assert dedupe_names(["a", "a", "", None, "a"]) == [
        "a",
        "a (2)",
        "Столбец 3",
        "Столбец 4",
        "a (3)",
    ]


def test_csv_cp1251_batches(tmp_path: Path):
    f = tmp_path / "выгрузка.csv"
    f.write_bytes("Дата;Сумма;Сумма\n01.01.2026;1 234,56;\n02.01.2026;;7\n".encode("cp1251"))
    r = CsvReader()
    assert r.can_read(f)
    opts = r.sniff(f, ReadOptions())
    assert (opts.encoding, opts.delimiter) == ("cp1251", ";")
    batches = list(r.batches(f, opts))
    rows = [row for b in batches for row in b.to_pylist()]
    assert rows == [
        {"Дата": "01.01.2026", "Сумма": "1 234,56", "Сумма (2)": None},
        {"Дата": "02.01.2026", "Сумма": None, "Сумма (2)": "7"},
    ]


def test_csv_header_row(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text("Отчёт за январь\nA;B\n1;2\n", encoding="utf-8")
    r = CsvReader()
    opts = r.sniff(f, ReadOptions(header_row=2))
    rows = [row for b in r.batches(f, opts) for row in b.to_pylist()]
    assert rows == [{"A": "1", "B": "2"}]


def test_csv_wrong_encoding_is_reported(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_bytes(b"A;B\n" + "да;нет\n".encode("cp1251"))
    r = CsvReader()
    with pytest.raises(AgenError, match="кодировке"):
        list(r.batches(f, ReadOptions(encoding="utf-8", delimiter=";")))


def test_excel_sheet_and_header_row(tmp_path: Path):
    f = tmp_path / "план.xlsx"
    wb = xlsxwriter.Workbook(str(f))
    ws = wb.add_worksheet("План")
    ws.write(0, 0, "Заголовок отчёта")
    ws.write_row(1, 0, ["Регион", "План"])
    ws.write_row(2, 0, ["Москва", 1000.5])
    wb.close()
    r = ExcelReader()
    assert r.can_read(f) and not CsvReader().can_read(f)
    rows = [row for b in r.batches(f, ReadOptions(sheet="План", header_row=2)) for row in b.to_pylist()]
    assert rows == [{"Регион": "Москва", "План": "1000.5"}]
    with pytest.raises(AgenError, match="нет листа"):
        list(r.batches(f, ReadOptions(sheet="Нет такого")))


# --- CSV: потоковое чтение -----------------------------------------------------------


def _rows(reader, f: Path, opts: ReadOptions | None = None) -> list[dict]:
    o = reader.sniff(f, opts or ReadOptions())
    return [row for b in reader.batches(f, o) for row in b.to_pylist()]


def test_csv_finds_header_below_report_title(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text("Отчёт о продажах;;\nза январь 2026;;\n\nДата;Регион;Сумма\n01.01.2026;Москва;10\n", encoding="utf-8")
    r = CsvReader()
    assert r.sniff(f, ReadOptions()).header_row == 4
    assert r.columns(f, ReadOptions()) == ["Дата", "Регион", "Сумма"]
    assert _rows(r, f) == [{"Дата": "01.01.2026", "Регион": "Москва", "Сумма": "10"}]


def test_csv_chunks_respect_quotes_and_line_breaks(tmp_path: Path):
    # Порции по 64 байта: строки и поля в кавычках (с «;» и переносом строки) режутся
    # на границах порций, но читаются целиком.
    lines = ["Номер;Комментарий;Сумма"]
    for i in range(200):
        comment = f'"Позвонить; клиент ""VIP""\nвторая строка {i}"' if i % 7 == 0 else f"просто {i}"
        lines.append(f"{i};{comment};{i},5")
    f = tmp_path / "x.csv"
    f.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1251"))
    r = CsvReader()
    r.chunk_bytes = 64
    rows = _rows(r, f)
    assert len(rows) == 200
    assert rows[7]["Комментарий"] == 'Позвонить; клиент "VIP"\nвторая строка 7'
    assert rows[199] == {"Номер": "199", "Комментарий": "просто 199", "Сумма": "199,5"}


def test_csv_progress_by_bytes(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text("a;b\n" + "".join(f"{i};{i}\n" for i in range(1000)), encoding="utf-8")
    r = CsvReader()
    r.chunk_bytes = 1024
    events = []
    opts = r.sniff(f, ReadOptions())
    list(r.batches(f, opts, progress=events.append))
    assert events and events[-1].done == events[-1].total == f.stat().st_size
    assert all(e.unit == "bytes" for e in events)


def test_csv_utf8_error_points_to_line_in_later_chunk(tmp_path: Path):
    body = "".join(f"{i};строка\n" for i in range(500))
    f = tmp_path / "x.csv"
    f.write_bytes(("a;b\n" + body).encode() + "401;сбой\n".encode("cp1251"))
    r = CsvReader()
    r.chunk_bytes = 512
    with pytest.raises(AgenError) as e:
        _rows(r, f, ReadOptions(encoding="utf-8"))
    assert e.value.details["line"] == 502
    assert "cp1251" in (e.value.hint or "")


def test_csv_stray_quote_and_ragged_line(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text('Товар;Цена\nМонитор 24";100\nКабель;5\n', encoding="utf-8")
    r = CsvReader()
    assert _rows(r, f, ReadOptions(quote=None)) == [
        {"Товар": 'Монитор 24"', "Цена": "100"},
        {"Товар": "Кабель", "Цена": "5"},
    ]
    g = tmp_path / "y.csv"
    g.write_text("a;b\n1;2\n3;4;5\n", encoding="utf-8")
    with pytest.raises(AgenError, match="строке 3") as e:
        _rows(r, g)
    assert e.value.details["line"] == 3


def test_csv_sample_takes_head_middle_tail(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text("n;v\n" + "".join(f"{i};{'x' * 40}\n" for i in range(150_000)), encoding="utf-8")
    r = CsvReader()
    s = r.sample(f, r.sniff(f, ReadOptions()), rows=1000)
    assert s.parts == ["начало", "середина", "конец"]
    n = [int(v) for v in s.table.column("n").to_pylist()]
    assert n[:3] == [0, 1, 2] and max(n) > 140_000 and any(60_000 < v < 90_000 for v in n)
    assert 140_000 < (s.rows_estimate or 0) < 160_000


# --- Excel -------------------------------------------------------------------------


def _xlsx(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    wb = xlsxwriter.Workbook(str(path))
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        for i, row in enumerate(rows):
            ws.write_row(i, 0, row)
    wb.close()
    return path


def test_excel_finds_header_and_continuation_sheets(tmp_path: Path):
    head = ["Дата", "Регион", "Сумма"]
    f = _xlsx(
        tmp_path / "выгрузка.xlsx",
        {
            "Пусто": [],
            "Часть 1": [["Выгрузка продаж"], [], head, ["01.01.2026", "Москва", 1], ["02.01.2026", "Казань", 2]],
            "Часть 2": [["Выгрузка продаж"], [], head, ["03.01.2026", "Тула", 3]],
            "Справочник": [["Код", "Название"], ["1", "Москва"]],
        },
    )
    r = ExcelReader()
    opts = r.sniff(f, ReadOptions())
    assert (opts.header_row, opts.sheets) == (3, ["Часть 1", "Часть 2"])
    assert r.columns(f, ReadOptions()) == head
    rows = [row for b in r.batches(f, opts) for row in b.to_pylist()]
    assert [x["Регион"] for x in rows] == ["Москва", "Казань", "Тула"]
    assert rows[0]["Сумма"] == "1"
    # Лист можно указать явно: тогда другие не добавляются.
    only = r.sniff(f, ReadOptions(sheet="Справочник"))
    assert (only.header_row, only.sheets) == (1, ["Справочник"])


def test_excel_sheets_with_other_header_are_rejected(tmp_path: Path):
    f = _xlsx(tmp_path / "x.xlsx", {"A": [["a", "b"], [1, 2]], "B": [["a", "c"], [3, 4]]})
    r = ExcelReader()
    with pytest.raises(AgenError, match="не такая"):
        list(r.batches(f, ReadOptions(sheet=["A", "B"], header_row=1)))


def test_excel_sample_and_dates(tmp_path: Path):
    import datetime as dt

    wb = xlsxwriter.Workbook(str(tmp_path / "x.xlsx"))
    ws = wb.add_worksheet()
    fmt = wb.add_format({"num_format": "dd.mm.yyyy"})
    ws.write_row(0, 0, ["Дата", "N"])
    for i in range(1, 20_001):
        ws.write_datetime(i, 0, dt.datetime(2026, 1, 1) + dt.timedelta(days=i % 31), fmt)
        ws.write_number(i, 1, i)
    wb.close()
    r = ExcelReader()
    f = tmp_path / "x.xlsx"
    s = r.sample(f, r.sniff(f, ReadOptions()), rows=1000)
    assert s.parts == ["начало", "середина", "конец"] and s.rows_estimate == 20_000
    assert s.table.column("Дата")[0].as_py().startswith("2026-01-02")
    assert s.table.column("N").to_pylist()[-1] == "20000"


def test_excel_empty_rows_above_header(tmp_path: Path):
    # Номер строки шапки считается от верха листа, хотя fastexcel сам пропустил бы пустые строки.
    f = _xlsx(tmp_path / "x.xlsx", {"A": [[], [], ["Регион", "Сумма"], ["Москва", 10]]})
    r = ExcelReader()
    assert r.sniff(f, ReadOptions()).header_row == 3
    assert _rows(r, f) == [{"Регион": "Москва", "Сумма": "10"}]


def test_excel_zip_bomb_is_rejected(tmp_path: Path, monkeypatch):
    import zipfile

    from autogenerator.readers_std import xlsx_head

    f = _xlsx(tmp_path / "x.xlsx", {"A": [["a"], [1]]})
    with zipfile.ZipFile(f, "a") as z:
        z.writestr("xl/media/zeros.bin", b"\0" * 1_000_000, compress_type=zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(xlsx_head, "ZIP_RATIO_MIN_BYTES", 100_000)
    with pytest.raises(AgenError, match="zip-бомб"):
        ExcelReader().sniff(f, ReadOptions())


# --- Excel 97–2003 (.xls) -----------------------------------------------------------


def test_xls_finds_header_below_parameters_and_continuation_sheet():
    r = XlsReader()
    assert r.can_read(XLS)
    assert not ExcelReader().can_read(XLS) and not CsvReader().can_read(XLS)
    opts = r.sniff(XLS, ReadOptions())
    assert (opts.header_row, opts.sheets) == (4, ["Часть 1", "Часть 2"])
    assert r.columns(XLS, ReadOptions()) == ["Номер", "Тема", "Ответов", "Часы", "Создано", "Закрыто"]
    batches = list(r.batches(XLS, opts, batch_rows=4))
    assert [b.num_rows for b in batches] == [4, 2, 4]  # порции — по листам
    rows = [row for b in batches for row in b.to_pylist()]
    assert [x["Номер"] for x in rows] == [str(n) for n in range(1001, 1011)]
    # Числа и даты — текстом, как у .xlsx: даты Excel — «ГГГГ-ММ-ДД чч:мм:сс».
    assert rows[0] == {
        "Номер": "1001",
        "Тема": "Не приходит письмо",
        "Ответов": "2",
        "Часы": "1.5",
        "Создано": "2026-01-09 00:00:00",
        "Закрыто": "2026-01-09 15:30:00",
    }
    assert (rows[3]["Часы"], rows[3]["Закрыто"]) == ("12.75", None)
    assert rows[-1]["Часы"] == "8"
    s = r.sample(XLS, opts)
    assert (s.parts, s.rows_estimate) == (["начало"], 10)
    assert s.table.column("Создано").to_pylist()[-1] == "2026-01-31 00:00:00"
    # Лист можно указать явно; строку заголовков — тоже, по счёту от верха листа.
    only = r.sniff(XLS, ReadOptions(sheet="Часть 2", header_row=4))
    assert [x["Номер"] for x in _rows(r, XLS, only)] == ["1007", "1008", "1009", "1010"]


def test_excel_reads_book_whatever_its_extension(tmp_path: Path):
    old = tmp_path / "обращения.xlsx"
    old.write_bytes(XLS.read_bytes())
    assert XlsReader().can_read(old) and not ExcelReader().can_read(old)
    assert len(_rows(XlsReader(), old)) == 10
    new = _xlsx(tmp_path / "план.xls", {"План": [["Регион", "План"], ["Москва", 1000.5]]})
    assert ExcelReader().can_read(new) and not XlsReader().can_read(new)
    assert _rows(ExcelReader(), new) == [{"Регион": "Москва", "План": "1000.5"}]


def test_xls_reader_skips_other_office_documents(tmp_path: Path):
    # Подпись OLE2 — у любого двоичного документа Office. В первом файле вместо потока
    # книги — поток с другим именем (как в документе Word), во втором за подписью — мусор.
    doc = tmp_path / "документ.xls"
    doc.write_bytes(XLS.read_bytes().replace("Workbook".encode("utf-16-le"), "Document".encode("utf-16-le")))
    junk = tmp_path / "мусор.xls"
    junk.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 1024)
    for f in (doc, junk):
        assert not XlsReader().can_read(f) and not CsvReader().can_read(f)
