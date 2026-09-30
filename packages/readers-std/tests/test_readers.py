from pathlib import Path

import pytest
import xlsxwriter

from autogenerator.contracts import AgenError, ReadOptions
from autogenerator.readers_std.csv_reader import CsvReader, detect_delimiter, detect_encoding
from autogenerator.readers_std.excel_reader import ExcelReader
from autogenerator.readers_std.names import dedupe_names


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
