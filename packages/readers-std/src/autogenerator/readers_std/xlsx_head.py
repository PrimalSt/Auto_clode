"""Первые строки листов .xlsx без загрузки всего листа.

fastexcel (calamine) читает лист только целиком: на листе в миллион строк это десятки
секунд и гигабайты памяти. Чтобы найти шапку, сравнить шапки листов и сверить столбцы
с источником, хватает первых строк: их даёт потоковый разбор XML листа, который
останавливается на нужной строке. Значения — как в файле: числа и даты Excel числами
(даты здесь не нужны), строки — текстом.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from xml.etree.ElementTree import Element, iterparse

from autogenerator.contracts import AgenError, ErrorCode

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

ZIP_RATIO_LIMIT = 100
"""Часть файла, сжатая сильнее 100:1, — признак zip-бомбы (раздел 12)."""
ZIP_RATIO_MIN_BYTES = 100 << 20
ZIP_TOTAL_LIMIT = 8 << 30
"""Предел распакованного XML. Лист в миллион строк × 30 столбцов — около 1,5 ГБ XML."""

_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


def check_zip(path: Path) -> None:
    """Отклонить zip-бомбу: слишком сильно сжатую часть или слишком большой объём XML."""
    total = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            total += info.file_size
            if (
                info.file_size >= ZIP_RATIO_MIN_BYTES
                and info.compress_size
                and info.file_size / info.compress_size > ZIP_RATIO_LIMIT
            ):
                raise AgenError(
                    ErrorCode.FILE_FORMAT,
                    f"Файл {path.name} отклонён: часть {info.filename} сжата сильнее {ZIP_RATIO_LIMIT}:1 "
                    "(так выглядят zip-бомбы)",
                )
    if total > ZIP_TOTAL_LIMIT:
        raise AgenError(
            ErrorCode.FILE_FORMAT,
            f"Файл {path.name} отклонён: в распакованном виде больше {ZIP_TOTAL_LIMIT >> 30} ГБ",
            hint="Выгрузку такого размера лучше получить в CSV.",
        )


def _col_index(ref: str) -> int:
    m = _CELL_REF.match(ref)
    letters = m.group(1) if m else "A"
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def sheet_paths(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Листы книги по порядку: (имя, путь XML внутри архива)."""
    rels: dict[str, str] = {}
    with z.open("xl/_rels/workbook.xml.rels") as f:
        for _, el in iterparse(f):
            if el.tag == f"{NS_PKG_REL}Relationship":
                target = el.get("Target", "")
                if target.startswith("/"):
                    target = target.lstrip("/")
                else:
                    target = posixpath.normpath(posixpath.join("xl", target))
                rels[el.get("Id", "")] = target
    out = []
    with z.open("xl/workbook.xml") as f:
        for _, el in iterparse(f):
            if el.tag == f"{NS_MAIN}sheet":
                rid = el.get(f"{NS_REL}id", "")
                if rid in rels:
                    out.append((el.get("name", ""), rels[rid]))
    return out


def _shared_strings(z: zipfile.ZipFile, upto: int) -> list[str]:
    """Общие строки книги до номера ``upto`` включительно (дальше не читаем)."""
    out: list[str] = []
    if upto < 0 or "xl/sharedStrings.xml" not in z.namelist():
        return out
    with z.open("xl/sharedStrings.xml") as f:
        for _, el in iterparse(f):
            if el.tag == f"{NS_MAIN}si":
                out.append("".join(t.text or "" for t in el.iter(f"{NS_MAIN}t")))
                el.clear()
                if len(out) > upto:
                    break
    return out


def _rows(z: zipfile.ZipFile, sheet_xml: str, n_rows: int) -> Iterator[tuple[int, list[tuple[int, str, str | None]]]]:
    """Строки листа: (номер строки с нуля, [(столбец, тип ячейки, значение)])."""
    with z.open(sheet_xml) as f:
        for _, el in iterparse(f):
            if el.tag != f"{NS_MAIN}row":
                continue
            r = int(el.get("r", "0")) - 1
            if r >= n_rows:
                break
            cells: list[tuple[int, str, str | None]] = []
            for c in el.iter(f"{NS_MAIN}c"):
                t = c.get("t", "n")
                if t == "inlineStr":
                    is_el: Element | None = c.find(f"{NS_MAIN}is")
                    value = "".join(x.text or "" for x in is_el.iter(f"{NS_MAIN}t")) if is_el is not None else None
                else:
                    v = c.find(f"{NS_MAIN}v")
                    value = v.text if v is not None else None
                cells.append((_col_index(c.get("r", "A1")), t, value))
            el.clear()
            yield r, cells


def head_rows(path: Path, sheet: str | int, n_rows: int = 30) -> list[list[str | None]]:
    """Первые ``n_rows`` строк листа (пустые строки — пустыми списками)."""
    with zipfile.ZipFile(path) as z:
        sheets = sheet_paths(z)
        xml = sheets[sheet][1] if isinstance(sheet, int) else next(p for name, p in sheets if name == sheet)
        raw = list(_rows(z, xml, n_rows))
        need = max((int(v) for _, cells in raw for _, t, v in cells if t == "s" and v is not None), default=-1)
        shared = _shared_strings(z, need)
    out: list[list[str | None]] = [[] for _ in range(min(n_rows, max((r for r, _ in raw), default=-1) + 1))]
    for r, cells in raw:
        width = max((i for i, _, _ in cells), default=-1) + 1
        row: list[str | None] = [None] * width
        for i, t, v in cells:
            if v is None:
                continue
            if t == "s":
                k = int(v)
                row[i] = shared[k] if k < len(shared) else None
            elif t == "b":
                row[i] = "true" if v == "1" else "false"
            else:
                row[i] = v
        out[r] = row
    return out


def sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return [name for name, _ in sheet_paths(z)]
