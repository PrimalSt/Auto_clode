"""Читатель CSV.

Файл читается потоково: порциями по десяткам мегабайт, выровненными по концу строки (с
учётом кавычек), и каждая порция разбирается Polars в несколько потоков. В памяти не бывает
больше пары порций, поэтому размер файла не ограничен (F-110).

Кодировка — UTF-8, UTF-8 с BOM или Windows-1251 (F-103). Она определяется по началу,
середине и концу файла; UTF-8 проверяется строго по всему файлу во время чтения, и при
ошибке загрузка останавливается с номером строки и предложением Windows-1251. Файлы
в Windows-1251 перекодируются порциями в памяти, без временного файла. Разделитель —
тот, что делит строки на одинаковое число полей; строка заголовков — первая, где
большинство ячеек заполнено текстом (над шапкой бывает заголовок отчёта).
"""

from __future__ import annotations

import codecs
import csv
import io
import os
from collections.abc import Iterator
from pathlib import Path

import polars as pl
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
from .threads import read_ahead

CHUNK_BYTES = 32 << 20
"""Сколько байт файла разбирается за раз. Порция строк — около 100 тыс. строк выгрузки."""
MAX_CARRY_CHUNKS = 8
"""Если за столько порций не нашлось конца строки вне кавычек, кавычки в файле непарные."""
PROBE_BYTES = 1 << 20
HEAD_TEXT_BYTES = 256 << 10
DELIMITERS = ";\t|,"
CP1251_NAMES = {"cp1251", "windows-1251", "win-1251", "1251"}


# --- кодировка ---------------------------------------------------------------------


def _norm_encoding(encoding: str) -> str:
    e = encoding.strip().lower().replace("_", "-")
    if e in CP1251_NAMES:
        return "cp1251"
    if e in ("utf8", "utf-8"):
        return "utf-8"
    if e in ("utf8-sig", "utf-8-sig"):
        return "utf-8-sig"
    try:
        return codecs.lookup(e).name
    except LookupError as err:
        raise AgenError(
            ErrorCode.FILE_ENCODING,
            f"Неизвестная кодировка «{encoding}»",
            hint="Поддерживаются utf-8, utf-8-sig и cp1251.",
        ) from err


def _utf8_evidence(chunk: bytes) -> bool | None:
    """Что говорит кусок файла о кодировке: ``True`` — это UTF-8 с русскими буквами,
    ``False`` — точно не UTF-8, ``None`` — только латиница, не понять."""
    if chunk.isascii():
        return None
    try:
        codecs.getincrementaldecoder("utf-8")().decode(chunk, final=False)
        return True
    except UnicodeDecodeError:
        return False


def _probes(path: Path) -> list[tuple[int, bytes]]:
    """Начало, середина и конец файла по мегабайту; середина и конец — с целой строки."""
    size = path.stat().st_size
    with path.open("rb") as f:
        out = [(0, f.read(PROBE_BYTES))]
        for start in (size // 2, size - PROBE_BYTES):
            if start <= PROBE_BYTES:
                continue
            f.seek(start)
            raw = f.read(PROBE_BYTES)
            nl = raw.find(b"\n")
            last = raw.rfind(b"\n")
            if 0 <= nl < last:
                out.append((start + nl + 1, raw[nl + 1 : last]))
    return out


def detect_encoding(head: bytes) -> str:
    """UTF-8 с BOM, UTF-8 или Windows-1251 — по куску файла."""
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    return "cp1251" if _utf8_evidence(head) is False else "utf-8"


def detect_file_encoding(path: Path) -> tuple[str, list[str]]:
    """Кодировка по началу, середине и концу файла и заметки о том, что настораживает."""
    probes = _probes(path)
    head = probes[0][1]
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig", []
    evidence = [(off, _utf8_evidence(chunk)) for off, chunk in probes]
    says = [e for _, e in evidence if e is not None]
    if not says or all(says):
        return "utf-8", []
    if not any(says):
        return "cp1251", []
    # Кодировка меняется по ходу файла.
    first = next(e for e in says)
    if first:
        # Начало в UTF-8: строгая проверка при чтении остановится на нужной строке.
        return "utf-8", ["Похоже, часть файла не в UTF-8: при чтении будет указана строка"]
    off = next(o for o, e in evidence if e is True)
    line = _line_at(path, off)
    raise AgenError(
        ErrorCode.FILE_ENCODING,
        f"В файле {path.name} меняется кодировка: начало в Windows-1251, а около строки {line} — UTF-8",
        details={"line": line},
        hint="Пересохраните выгрузку в одной кодировке.",
    )


def _line_at(path: Path, offset: int) -> int:
    n = 1
    with path.open("rb") as f:
        left = offset
        while left > 0:
            block = f.read(min(left, 16 << 20))
            if not block:
                break
            n += block.count(b"\n")
            left -= len(block)
    return n


def _decoder_error(path: Path, encoding: str, data: bytes, err: UnicodeDecodeError, first_line: int) -> AgenError:
    line = first_line + data[: err.start].count(b"\n")
    bad = data[err.start : err.start + 4].hex(" ")
    if encoding == "cp1251":
        hint = "Похоже, файл не в Windows-1251: укажите encoding: utf-8 в настройках источника или --encoding utf-8."
    else:
        hint = (
            "Похоже, файл (или его часть) в Windows-1251: укажите encoding: cp1251 в настройках источника "
            "или --encoding cp1251."
        )
    return AgenError(
        ErrorCode.FILE_ENCODING,
        f"В строке {line} файла {path.name} символы не в кодировке {encoding} (байты {bad})",
        details={"line": line, "encoding": encoding},
        hint=hint,
    )


# --- разделитель и шапка --------------------------------------------------------------


def _split(lines: list[str], delimiter: str, quote: str | None) -> list[list[str]]:
    reader = csv.reader(lines, delimiter=delimiter, quotechar=quote or "\x00", quoting=csv.QUOTE_MINIMAL)
    try:
        return [row for row in reader]
    except csv.Error:
        return [ln.split(delimiter) for ln in lines]


def detect_delimiter(sample: str, quote: str | None = '"') -> str:
    """Разделитель, который делит строки на одинаковое число полей (больше одного).

    ``csv.Sniffer`` здесь ненадёжен: в русских выгрузках запятая стоит и в дробях («12,5»),
    и в названиях («Сумма, руб.»). Строки с заголовком отчёта над шапкой в счёт не идут:
    побеждает разделитель, при котором больше всего строк одной ширины. При равенстве —
    более вероятный для русских выгрузок: «;», табуляция, «|», запятая.
    """
    lines = [ln for ln in sample.splitlines() if ln.strip()][:200]
    if len(lines) > 2:
        lines = lines[:-1]  # последняя строка выборки могла обрезаться
    best, best_score = ";", (0, 0)
    for d in DELIMITERS:
        widths = [len(r) for r in _split(lines, d, quote)]
        multi = [w for w in widths if w > 1]
        if not multi:
            continue
        mode = max(set(multi), key=lambda w: (multi.count(w), w))
        score = (multi.count(mode), mode)
        if score > best_score:
            best, best_score = d, score
    return best


def _head_text(path: Path, encoding: str) -> str:
    with path.open("rb") as f:
        raw = f.read(HEAD_TEXT_BYTES)
    if encoding == "utf-8-sig" and raw.startswith(codecs.BOM_UTF8):
        raw = raw[3:]
    dec = codecs.getincrementaldecoder("utf-8" if encoding == "utf-8-sig" else encoding)(errors="replace")
    return dec.decode(raw, final=False)


# --- порции строк ------------------------------------------------------------------------


def _row_end(buf: bytes | bytearray, start: int, quote: bytes | None) -> int:
    """Позиция сразу после конца строки, начатой в ``start`` (перевод строки вне кавычек);
    -1 — строка не закончилась в буфере."""
    pos = start
    while True:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            return -1
        if quote is None or buf.count(quote, start, nl) % 2 == 0:
            return nl + 1
        pos = nl + 1


def _last_row_end(buf: bytes | bytearray, quote: bytes | None) -> int:
    """Конец последней целой строки в буфере: последний перевод строки, перед которым
    чётное число кавычек. -1 — такого нет."""
    nl = buf.rfind(b"\n")
    if nl < 0 or quote is None:
        return nl + 1 if nl >= 0 else -1
    q = buf.count(quote, 0, nl)
    for _ in range(10_000):
        if q % 2 == 0:
            return nl + 1
        prev = buf.rfind(b"\n", 0, nl)
        if prev < 0:
            return -1
        q -= buf.count(quote, prev, nl)
        nl = prev
    return -1


class _Chunk:
    """Порция строк файла в UTF-8. ``offset`` — где она начинается в файле (байт), ``line`` —
    номер её первой строки, если он уже известен (иначе считается только при ошибке:
    подсчёт переводов строк по всему файлу стоил бы заметного времени)."""

    __slots__ = ("consumed", "data", "line", "offset")

    def __init__(self, data: bytes, offset: int, consumed: int, line: int | None = None):
        self.data = data
        self.offset = offset
        self.consumed = consumed
        self.line = line

    def first_line(self, path: Path) -> int:
        if self.line is None:
            self.line = _line_at(path, self.offset)
        return self.line


def _chunks(
    path: Path,
    encoding: str,
    skip_rows: int,
    quote: str | None,
    chunk_bytes: int = CHUNK_BYTES,
) -> Iterator[_Chunk]:
    """Порции файла в UTF-8, выровненные по концу строки, после ``skip_rows`` строк шапки."""
    q = quote.encode() if quote else None
    transcode = encoding == "cp1251"
    with path.open("rb") as f:
        buf = bytearray(f.read(chunk_bytes))
        offset = 0
        if encoding == "utf-8-sig" and buf.startswith(codecs.BOM_UTF8):
            del buf[:3]
            offset = 3
        # Пропустить строки над данными: заголовок отчёта и шапку.
        pos = 0
        line = 1
        for _ in range(skip_rows):
            end = _row_end(buf, pos, q)
            while end < 0:
                more = f.read(chunk_bytes)
                if not more:
                    end = len(buf)
                    break
                buf += more
                end = _row_end(buf, pos, q)
            line += buf.count(b"\n", pos, end)
            pos = end
        offset += pos
        del buf[:pos]
        first: int | None = line
        carry_limit = MAX_CARRY_CHUNKS * chunk_bytes
        eof = False
        while True:
            while len(buf) < chunk_bytes and not eof:
                more = f.read(max(chunk_bytes - len(buf), 1 << 16))
                if not more:
                    eof = True
                    break
                buf += more
            if not buf:
                return
            cut = len(buf) if eof else _last_row_end(buf, q)
            while cut <= 0:
                more = f.read(chunk_bytes)
                if not more:
                    eof = True
                    cut = len(buf)
                    break
                buf += more
                if len(buf) > carry_limit:
                    # Кавычки непарные: режем по последнему переводу строки, а ошибку формата
                    # (если она есть) назовёт разбор порции.
                    cut = buf.rfind(b"\n") + 1 or len(buf)
                    break
                cut = _last_row_end(buf, q)
            with memoryview(buf) as mv:
                data = bytes(mv[:cut])
            del buf[:cut]
            start, offset = offset, offset + cut
            if cut < 64 and not data.strip():
                continue
            if transcode:
                try:
                    data = data.decode("cp1251").encode("utf-8")
                except UnicodeDecodeError as e:
                    raise _decoder_error(path, encoding, data, e, _line_at(path, start)) from e
            yield _Chunk(data, start, offset, first)
            first = None


def _locate_ragged(data: bytes, first_line: int, delimiter: str, quote: str | None, width: int) -> tuple[int, int]:
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    reader = csv.reader(lines, delimiter=delimiter, quotechar=quote or "\x00")
    for row in reader:
        if len(row) > width:
            return first_line + reader.line_num - 1, len(row)
    return first_line, width + 1


def _locate_quotes(data: bytes, first_line: int, quote: str | None) -> int:
    if not quote:
        return first_line
    q = quote.encode()
    depth = 0
    for i, ln in enumerate(data.split(b"\n")):
        depth = (depth + ln.count(q)) % 2
        if depth:
            return first_line + i
    return first_line


def _locate_utf8(data: bytes, first_line: int) -> tuple[int, str]:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        return first_line + data[: e.start].count(b"\n"), data[e.start : e.start + 4].hex(" ")
    return first_line, ""


class CsvReader(ReaderPlugin):
    name = "csv"
    title = "CSV"
    formats = ("csv", "txt", "tsv")

    chunk_bytes: int = CHUNK_BYTES

    def can_read(self, path: Path) -> bool:
        with path.open("rb") as f:
            head = f.read(4096)
        if head.startswith(b"PK\x03\x04") or head.startswith(b"\xd0\xcf\x11\xe0"):
            return False  # zip (xlsx) или старый двоичный формат Office (xls)
        if path.suffix.lower() in (".csv", ".txt", ".tsv"):
            return True
        return b"\x00" not in head

    def sniff(self, path: Path, options: ReadOptions) -> ReadOptions:
        if options.encoding:
            encoding = _norm_encoding(options.encoding)
        else:
            encoding, _ = detect_file_encoding(path)
        text = _head_text(path, encoding)
        lines = text.splitlines()
        if len(lines) > 1 and not text.endswith(("\n", "\r")):
            lines = lines[:-1]
        quote = options.quote
        delimiter = options.delimiter or detect_delimiter("\n".join(lines[:200]), quote)
        header_row = options.header_row
        if header_row is None:
            rows = _split(lines[:60], delimiter, quote)
            width = data_width(rows[: min(len(rows), 60)])
            header_row = detect_header_row(rows, width) or 1
        return ReadOptions(encoding=encoding, delimiter=delimiter, quote=quote, header_row=header_row, sheet=None)

    def columns(self, path: Path, options: ReadOptions) -> list[str]:
        if not (options.encoding and options.delimiter and options.header_row):
            options = self.sniff(path, options)
        assert options.encoding and options.delimiter and options.header_row
        enc = "utf-8-sig" if options.encoding == "utf-8-sig" else options.encoding
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.reader(f, delimiter=options.delimiter, quotechar=options.quote or "\x00")
                for i, row in enumerate(reader, start=1):
                    if i == options.header_row:
                        return dedupe_names(row)
        except UnicodeDecodeError as e:
            raise AgenError(
                ErrorCode.FILE_ENCODING,
                f"В шапке файла {path.name} символы не в кодировке {options.encoding}",
                hint="Укажите кодировку явно: encoding: cp1251 или utf-8.",
            ) from e
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {path.name} нет строки заголовков {options.header_row}")

    def _parse(self, path: Path, chunk: _Chunk, names: list[str], opts: ReadOptions) -> pl.DataFrame:
        assert opts.delimiter
        try:
            return pl.read_csv(
                io.BytesIO(chunk.data),
                has_header=False,
                schema=dict.fromkeys(names, pl.String),
                separator=opts.delimiter,
                quote_char=opts.quote or None,
                raise_if_empty=False,
                truncate_ragged_lines=False,
            )
        except pl.exceptions.ComputeError as e:
            msg = str(e)
            if "utf-8" in msg.lower() or "utf8" in msg.lower():
                line, bad = _locate_utf8(chunk.data, chunk.first_line(path))
                raise AgenError(
                    ErrorCode.FILE_ENCODING,
                    f"В строке {line} файла {path.name} символы не в кодировке UTF-8 (байты {bad})",
                    details={"line": line, "encoding": opts.encoding},
                    hint="Похоже, часть файла в Windows-1251: укажите encoding: cp1251 в настройках источника "
                    "или --encoding cp1251.",
                ) from e
            if "more fields" in msg:
                line, got = _locate_ragged(chunk.data, chunk.first_line(path), opts.delimiter, opts.quote, len(names))
                raise AgenError(
                    ErrorCode.FILE_FORMAT,
                    f"В строке {line} файла {path.name} {got} полей, а в шапке {len(names)}",
                    details={"line": line},
                    hint="Проверьте разделитель (delimiter) и кавычки: возможно, разделитель встречается внутри "
                    "текста без кавычек.",
                ) from e
            if "malformed" in msg.lower() or "quote" in msg.lower():
                line = _locate_quotes(chunk.data, chunk.first_line(path), opts.quote)
                raise AgenError(
                    ErrorCode.FILE_FORMAT,
                    f"В файле {path.name} непарные кавычки около строки {line}",
                    details={"line": line},
                    hint='Если кавычки в файле — обычный символ (например, 24" монитор), укажите quote: null в '
                    "параметрах чтения источника или --no-quote.",
                ) from e
            raise AgenError(ErrorCode.FILE_FORMAT, f"Не удалось прочитать {path.name}: {msg}") from e

    def batches(
        self,
        path: Path,
        options: ReadOptions,
        batch_rows: int = 100_000,
        progress: ProgressCallback | None = None,
    ) -> Iterator[pa.RecordBatch]:
        opts = options if options.encoding and options.delimiter and options.header_row else self.sniff(path, options)
        assert opts.encoding and opts.header_row
        names = self.columns(path, opts)
        total = os.path.getsize(path)
        chunks = read_ahead(_chunks(path, opts.encoding, opts.header_row, opts.quote, self.chunk_bytes), depth=2)
        for chunk in chunks:
            df = self._parse(path, chunk, names, opts)
            if progress is not None:
                progress(ReadProgress("чтение", chunk.consumed, total, "bytes"))
            if df.height:
                yield from df.to_arrow(compat_level=pl.CompatLevel.newest()).to_batches(max_chunksize=batch_rows)

    def sample(self, path: Path, options: ReadOptions, rows: int = 10_000) -> SampleTable:
        """Начало файла плюс порции из середины и конца (типы в конце выгрузки бывают другими)."""
        opts = options if options.encoding and options.delimiter and options.header_row else self.sniff(path, options)
        assert opts.encoding and opts.header_row
        names = self.columns(path, opts)
        size = os.path.getsize(path)
        head: list[pl.DataFrame] = []
        got = 0
        head_end = 0
        read_rows = 0
        for chunk in _chunks(path, opts.encoding, opts.header_row, opts.quote, min(self.chunk_bytes, PROBE_BYTES)):
            df = self._parse(path, chunk, names, opts)
            read_rows += df.height
            head.append(df.head(rows - got))
            got += head[-1].height
            head_end = chunk.consumed
            if got >= rows:
                break
        parts = ["начало"]
        frames = head
        estimate = got
        if head_end < size and read_rows:
            # Оценка по байтам файла (после перекодировки байтов было бы больше).
            avg = head_end / read_rows
            estimate = int((size - head_end) / max(avg, 1)) + read_rows
            for label, start in (("середина", size // 2), ("конец", size - 2 * PROBE_BYTES)):
                if start <= head_end:
                    continue
                probe = self._probe_rows(path, start, names, opts)
                if probe is not None and probe.height:
                    n = max(rows // 4, 100)
                    frames.append(probe.tail(n) if label == "конец" else probe.head(n))
                    parts.append(label)
        table = pl.concat(frames, how="vertical") if frames else pl.DataFrame(schema=dict.fromkeys(names, pl.String))
        return SampleTable(table.to_arrow(), parts, estimate)

    def _probe_rows(self, path: Path, start: int, names: list[str], opts: ReadOptions) -> pl.DataFrame | None:
        assert opts.encoding
        with path.open("rb") as f:
            f.seek(start)
            raw = f.read(2 * PROBE_BYTES)
        nl = raw.find(b"\n")
        last = raw.rfind(b"\n")
        if nl < 0 or last <= nl:
            return None
        data = raw[nl + 1 : last + 1]
        enc = "utf-8" if opts.encoding == "utf-8-sig" else opts.encoding
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            return None
        try:
            # Кусок мог начаться внутри поля в кавычках: тогда такой кусок просто пропускаем.
            return self._parse(path, _Chunk(text.encode("utf-8"), start, start), names, opts)
        except AgenError:
            return None
