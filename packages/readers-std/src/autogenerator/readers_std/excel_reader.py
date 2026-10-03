"""Читатель Excel (.xlsx) на fastexcel.

Лист читается целиком (предел формата — 1 048 576 строк) и отдаётся порциями; все ячейки
приводятся к тексту: даты Excel становятся строками «2026-01-01 00:00:00», и ``ingest``
разбирает их вместе с остальными форматами.

Шапка ищется по первым строкам листа (над ней бывает заголовок отчёта), а листы
выбираются так (F-104, F-111): если лист не указан, в выгрузку входят первый лист с
данными и все следующие листы с такой же шапкой — так учётные системы разбивают выгрузки
больше миллиона строк. Листы читаются по очереди, в памяти один лист. Перед чтением
файл проверяется на zip-бомбу. Объединённые ячейки шапки — v1.

Те же правила у .xls (``xls_reader.py``): там свои только методы доступа к книге.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import fastexcel
import pyarrow as pa

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    ProgressCallback,
    ReaderPlugin,
    ReadOptions,
    ReadProgress,
    SampleTable,
)

from .names import data_width, dedupe_names, detect_header_row
from .xlsx_head import check_zip, head_rows, sheet_names

HEAD_ROWS = 30


def _norm_header(cells: list[str | None]) -> list[str]:
    out = [(c or "").strip().lower() for c in cells]
    while out and not out[-1]:
        out.pop()
    return out


class ExcelReader(ReaderPlugin):
    name = "xlsx"
    title = "Excel"
    formats: ClassVar[tuple[str, ...]] = ("xlsx", "xlsm")
    sample_reads_all = True

    def can_read(self, path: Path) -> bool:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as z:
            return "xl/workbook.xml" in z.namelist()

    # --- доступ к книге (у .xls — свой, xls_reader.py) ---------------------------------

    def _check_file(self, path: Path) -> None:
        check_zip(path)

    def _sheet_names(self, path: Path) -> list[str]:
        return sheet_names(path)

    def _head_rows(self, path: Path, sheet: str, n_rows: int) -> list[list[str | None]]:
        return head_rows(path, sheet, n_rows)

    def _open(self, path: Path) -> fastexcel.ExcelReader:
        # fastexcel выбирает формат книги по расширению. Если оно чужое (.xlsx, сохранённый
        # как .xls, и наоборот), книга открывается из байтов: так формат узнаётся по содержимому.
        if path.suffix.lower().lstrip(".") in self.formats:
            return fastexcel.read_excel(str(path))
        return fastexcel.read_excel(path.read_bytes())

    # --- какие листы и где шапка --------------------------------------------------------

    def _resolve_sheet(self, path: Path, names: list[str], ref: str | int) -> str:
        if isinstance(ref, int):
            if not 0 <= ref < len(names):
                raise AgenError(
                    ErrorCode.FILE_FORMAT,
                    f"В файле {path.name} нет листа номер {ref} (листов: {len(names)}; нумерация с нуля)",
                )
            return names[ref]
        if ref not in names:
            raise AgenError(
                ErrorCode.FILE_FORMAT,
                f"В файле {path.name} нет листа «{ref}». Листы: {', '.join(names)}",
            )
        return ref

    def sniff(self, path: Path, options: ReadOptions) -> ReadOptions:
        self._check_file(path)
        names = self._sheet_names(path)
        if not names:
            raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {path.name} нет листов")
        heads: dict[str, list[list[str | None]]] = {}
        if options.sheets:
            chosen = [self._resolve_sheet(path, names, s) for s in options.sheets]
        else:
            chosen = []
            for name in names:
                heads[name] = self._head_rows(path, name, HEAD_ROWS)
                if any(any(c not in (None, "") for c in r) for r in heads[name]):
                    chosen = [name]
                    break
            if not chosen:
                raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {path.name} все листы пустые")
        first = chosen[0]
        rows = heads.get(first) or self._head_rows(path, first, HEAD_ROWS)
        header_row = options.header_row or detect_header_row(rows, data_width(rows)) or 1
        if not options.sheets:
            # Следующие листы с той же шапкой — продолжение выгрузки.
            header = _norm_header(rows[header_row - 1] if header_row <= len(rows) else [])
            for name in names[names.index(first) + 1 :]:
                other = self._head_rows(path, name, header_row)
                if header and len(other) >= header_row and _norm_header(other[header_row - 1]) == header:
                    chosen.append(name)
        sheet: str | list[str | int] = chosen[0] if len(chosen) == 1 else list(chosen)
        return ReadOptions(encoding=None, delimiter=None, quote=options.quote, header_row=header_row, sheet=sheet)

    # --- чтение -----------------------------------------------------------------------

    def _load(self, path: Path, sheet: str, header_row: int) -> tuple[list[str], pa.RecordBatch]:
        try:
            # skip_rows=0: строки считаются от верха листа, как в шапке из _head_rows
            # (иначе fastexcel пропускает пустые строки над данными и номер шапки сдвигается).
            batch = self._open(path).load_sheet(sheet, header_row=None, skip_rows=0, dtypes="string", eager=True)
        except Exception as e:
            raise AgenError(ErrorCode.FILE_FORMAT, f"Не удалось прочитать лист «{sheet}» файла {path.name}: {e}") from e
        assert isinstance(batch, pa.RecordBatch)
        if batch.num_rows < header_row:
            return [], batch.slice(0, 0)
        head = [batch.column(i)[header_row - 1].as_py() for i in range(batch.num_columns)]
        width = len(head)
        while width and not (head[width - 1] or "").strip():
            # Справа от шапки — пустые столбцы; данные в них (если есть) без названия.
            col = batch.column(width - 1).slice(header_row)
            if col.null_count < len(col):
                break
            width -= 1
        names = dedupe_names(head[:width])
        data = batch.slice(header_row)
        arrays = [data.column(i).cast(pa.string()) for i in range(width)]
        return names, pa.RecordBatch.from_arrays(arrays, names=names)

    def batches(
        self,
        path: Path,
        options: ReadOptions,
        batch_rows: int = 100_000,
        progress: ProgressCallback | None = None,
    ) -> Iterator[pa.RecordBatch]:
        opts = options if options.header_row and options.sheet is not None else self.sniff(path, options)
        assert opts.header_row
        names_all = self._sheet_names(path)
        sheets = [self._resolve_sheet(path, names_all, s) for s in opts.sheets or [0]]
        first_names: list[str] | None = None
        for i, sheet in enumerate(sheets, start=1):
            if progress is not None:
                progress(ReadProgress(f"чтение листа «{sheet}» ({i} из {len(sheets)})", i - 1, len(sheets), "sheets"))
            names, batch = self._load(path, sheet, opts.header_row)
            if first_names is None:
                first_names = names
            elif names != first_names:
                raise AgenError(
                    ErrorCode.FILE_FORMAT,
                    f"Шапка листа «{sheet}» файла {path.name} не такая, как у листа «{sheets[0]}»",
                    hint="В одну выгрузку входят листы с одинаковыми столбцами; укажите лист явно (sheet).",
                )
            yield from _slices(batch, batch_rows)
            del batch
        if progress is not None:
            progress(ReadProgress("листы прочитаны", len(sheets), len(sheets), "sheets"))

    def columns(self, path: Path, options: ReadOptions) -> list[str]:
        """Шапка без чтения листа: только первые строки (у .xlsx — потоковым разбором XML)."""
        opts = options if options.header_row and options.sheet is not None else self.sniff(path, options)
        assert opts.header_row
        first = self._resolve_sheet(path, self._sheet_names(path), (opts.sheets or [0])[0])
        rows = self._head_rows(path, first, opts.header_row)
        head = rows[opts.header_row - 1] if len(rows) >= opts.header_row else []
        width = len(head)
        while width and not (head[width - 1] or "").strip():
            width -= 1
        return dedupe_names(head[:width])

    def sample(self, path: Path, options: ReadOptions, rows: int = 10_000) -> SampleTable:
        """Начало, середина и конец выгрузки: лист всё равно читается целиком."""
        opts = options if options.header_row and options.sheet is not None else self.sniff(path, options)
        batches = list(self.batches(path, opts, batch_rows=1 << 30))
        table = pa.Table.from_batches(batches) if batches else pa.table({})
        n = table.num_rows
        parts = ["начало"]
        if n <= rows:
            return SampleTable(table, parts, n)
        quarter = max(rows // 4, 100)
        pieces = [table.slice(0, rows), table.slice(n // 2, quarter), table.slice(n - quarter, quarter)]
        parts += ["середина", "конец"]
        return SampleTable(pa.concat_tables(pieces), parts, n)


def _slices(batch: pa.RecordBatch, size: int) -> Iterator[pa.RecordBatch]:
    for start in range(0, batch.num_rows, size):
        yield batch.slice(start, size)
