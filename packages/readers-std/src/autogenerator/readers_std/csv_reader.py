"""Читатель CSV.

Файл читается потоково порциями (pyarrow), в память целиком не загружается. Кодировка —
UTF-8, UTF-8 с BOM или Windows-1251; разделитель определяется ``csv.Sniffer`` по первым
64 КБ (ARCHITECTURE.md, раздел 6.1).

На этапе M0 кодировка определяется по первому мегабайту; строгая проверка UTF-8 по всему
файлу с номером строки ошибки — на этапе M1.
"""

from __future__ import annotations

import codecs
import csv
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv

from autogenerator.contracts import AgenError, ErrorCode, ReaderPlugin, ReadOptions

from .names import dedupe_names

SNIFF_BYTES = 64 * 1024
ENCODING_PROBE_BYTES = 1024 * 1024
DELIMITERS = ";\t|,"


def detect_encoding(head: bytes) -> str:
    """UTF-8 с BOM, UTF-8 или Windows-1251 — по началу файла."""
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        # final=False: последний символ мог обрезаться посередине — это не ошибка.
        decoder.decode(head, final=False)
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"


def detect_delimiter(sample: str) -> str:
    """Разделитель, который делит строки начала файла на одинаковое число полей.

    ``csv.Sniffer`` здесь ненадёжен: в русских выгрузках запятая стоит и в дробях («12,5»),
    и в названиях («Сумма, руб.»). При равенстве выигрывает более вероятный для русских
    выгрузок разделитель: «;», табуляция, «|», запятая.
    """
    lines = [ln for ln in sample.splitlines() if ln.strip()][:100]
    if len(lines) > 2:
        lines = lines[:-1]  # последняя строка выборки могла обрезаться
    best, best_score = ";", 0.0
    for d in DELIMITERS:
        counts = [len(next(csv.reader([ln], delimiter=d))) - 1 for ln in lines]
        if not counts or counts[0] == 0:
            continue
        score = sum(1 for c in counts if c == counts[0]) / len(counts)
        if score > best_score:
            best, best_score = d, score
    return best


def _pyarrow_encoding(encoding: str) -> str:
    # pyarrow сам пропускает BOM у utf-8; utf-8-sig он не знает.
    return "utf-8" if encoding.lower().replace("_", "-") in ("utf-8-sig", "utf8-sig") else encoding


def _encoding_error(path: Path, encoding: str) -> AgenError:
    return AgenError(
        ErrorCode.FILE_ENCODING,
        f"В файле {path.name} встретились символы не в кодировке {encoding}",
        hint="Укажите кодировку явно (encoding: cp1251 или utf-8) в настройках источника.",
    )


class CsvReader(ReaderPlugin):
    name = "csv"
    title = "CSV"
    formats = ("csv",)

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() in (".csv", ".txt", ".tsv"):
            return True
        with path.open("rb") as f:
            head = f.read(4096)
        # Текстовый файл без нулевых байтов и не zip (xlsx) — пробуем как CSV.
        return b"\x00" not in head and not head.startswith(b"PK\x03\x04")

    def sniff(self, path: Path, options: ReadOptions) -> ReadOptions:
        with path.open("rb") as f:
            head = f.read(ENCODING_PROBE_BYTES)
        encoding = options.encoding or detect_encoding(head)
        text = head[:SNIFF_BYTES].decode(encoding, errors="replace")
        lines = text.splitlines()[options.header_row - 1 :]
        delimiter = options.delimiter or detect_delimiter("\n".join(lines[:200]))
        return ReadOptions(
            encoding=encoding,
            delimiter=delimiter,
            header_row=options.header_row,
            sheet=None,
        )

    def header(self, path: Path, options: ReadOptions) -> list[str]:
        assert options.encoding and options.delimiter
        try:
            with path.open("r", encoding=options.encoding, newline="") as f:
                reader = csv.reader(f, delimiter=options.delimiter)
                for i, row in enumerate(reader, start=1):
                    if i == options.header_row:
                        return dedupe_names(row)
        except UnicodeDecodeError as e:
            raise _encoding_error(path, options.encoding) from e
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {path.name} нет строки заголовков {options.header_row}")

    def batches(self, path: Path, options: ReadOptions, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]:
        opts = self.sniff(path, options)
        names = self.header(path, opts)
        assert opts.encoding and opts.delimiter
        # Размер блока задаётся в байтах; ~200 байт на строку — грубая оценка.
        block = max(1 << 20, min(64 << 20, batch_rows * 200))
        try:
            reader = pacsv.open_csv(
                path,
                read_options=pacsv.ReadOptions(
                    encoding=_pyarrow_encoding(opts.encoding),
                    column_names=names,
                    skip_rows=opts.header_row,
                    block_size=block,
                ),
                parse_options=pacsv.ParseOptions(delimiter=opts.delimiter),
                convert_options=pacsv.ConvertOptions(
                    column_types={n: pa.string() for n in names},
                    strings_can_be_null=True,
                    null_values=[""],
                    quoted_strings_can_be_null=True,
                ),
            )
            yield from reader
        except pa.ArrowInvalid as e:
            msg = str(e)
            if "UTF8" in msg or "utf8" in msg.lower():
                raise _encoding_error(path, opts.encoding) from e
            raise AgenError(ErrorCode.FILE_FORMAT, f"Не удалось прочитать {path.name}: {msg}") from e
