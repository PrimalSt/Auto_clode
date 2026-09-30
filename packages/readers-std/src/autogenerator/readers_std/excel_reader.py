"""Читатель Excel (.xlsx) на fastexcel.

Лист читается целиком (предел формата — 1 048 576 строк) и отдаётся порциями. Все ячейки
приводятся к тексту; даты Excel становятся строками «2026-01-01 00:00:00», и ``ingest``
разбирает их вместе с остальными форматами. Многолистовые выгрузки и объединённые ячейки —
на этапах M1 и v1.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path

import fastexcel
import pyarrow as pa

from autogenerator.contracts import AgenError, ErrorCode, ReaderPlugin, ReadOptions

from .names import dedupe_names


class ExcelReader(ReaderPlugin):
    name = "xlsx"
    title = "Excel"
    formats = ("xlsx", "xlsm")

    def can_read(self, path: Path) -> bool:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as z:
            return "xl/workbook.xml" in z.namelist()

    def sniff(self, path: Path, options: ReadOptions) -> ReadOptions:
        sheet = options.sheet if options.sheet is not None else 0
        return ReadOptions(encoding=None, delimiter=None, header_row=options.header_row, sheet=sheet)

    def batches(self, path: Path, options: ReadOptions, batch_rows: int = 100_000) -> Iterator[pa.RecordBatch]:
        opts = self.sniff(path, options)
        try:
            reader = fastexcel.read_excel(str(path))
            sheet_ref = opts.sheet if opts.sheet is not None else 0
            if isinstance(sheet_ref, str) and sheet_ref not in reader.sheet_names:
                raise AgenError(
                    ErrorCode.FILE_FORMAT,
                    f"В файле {path.name} нет листа «{sheet_ref}». Листы: {', '.join(reader.sheet_names)}",
                )
            sheet = reader.load_sheet(sheet_ref, header_row=opts.header_row - 1, dtypes="string")
            table = pa.Table.from_batches([sheet.to_arrow()])
        except AgenError:
            raise
        except Exception as e:
            raise AgenError(ErrorCode.FILE_FORMAT, f"Не удалось прочитать {path.name}: {e}") from e
        names = dedupe_names([str(n) if n is not None else None for n in table.column_names])
        table = table.rename_columns(names)
        # Все столбцы — nullable string, как у CSV.
        table = table.cast(pa.schema([pa.field(n, pa.string()) for n in names]))
        yield from table.to_batches(max_chunksize=batch_rows)
