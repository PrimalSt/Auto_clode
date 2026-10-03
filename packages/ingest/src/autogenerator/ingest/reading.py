"""Выбор читателя и снимок структуры файла: как он прочитан, столбцы, выведенные типы,
профиль по выборке и первые строки."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import polars as pl

from autogenerator.contracts import (
    AgenError,
    ColumnSnapshot,
    DType,
    ErrorCode,
    PluginKind,
    ReaderPlugin,
    ReadOptions,
    SchemaSnapshot,
)
from autogenerator.plugin_host import PluginRegistry

from .casting import cast_expr, infer_dtype_share
from .profile import profile_frame

SAMPLE_ROWS = 10_000
PREVIEW_ROWS = 100
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def choose_reader(path: Path, registry: PluginRegistry, fmt: str | None = None) -> ReaderPlugin:
    """Читатель по явному формату или по файлу: сначала те, чьё расширение совпало."""
    if not path.exists():
        raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {path}")
    if path.is_dir():
        raise AgenError(ErrorCode.FILE_FORMAT, f"{path} — папка, а не файл выгрузки")
    if fmt:
        return registry.reader(fmt)
    readers: list[ReaderPlugin] = registry.all(PluginKind.READER)
    suffix = path.suffix.lower().lstrip(".")
    ordered = [r for r in readers if suffix in r.formats] + [r for r in readers if suffix not in r.formats]
    for r in ordered:
        try:
            if r.can_read(path):
                return r
        except AgenError:
            raise
        except Exception:
            continue
    with path.open("rb") as f:
        head = f.read(8)
    if head == OLE2_SIGNATURE:
        # Такая подпись не только у .xls (их узнаёт читатель), но и у документов Word, писем
        # Outlook и книг, зашифрованных паролем.
        raise AgenError(
            ErrorCode.FILE_FORMAT,
            f"{path.name} не читается как книга Excel: это документ Office другого вида "
            "(Word, Outlook) или книга, защищённая паролем",
            hint="Если это книга Excel, откройте её в Excel, снимите пароль и сохраните как .xlsx (Книга Excel).",
        )
    if zipfile.is_zipfile(path):
        # Книги .xlsx и .xlsb — тоже zip (их узнают читатели), а в этом книги нет.
        raise AgenError(
            ErrorCode.FILE_FORMAT,
            f"{path.name} не читается как книга Excel: это zip-архив без книги "
            "(документ Word или PowerPoint, таблица .ods, архив с файлами)",
            hint="Если в архиве выгрузка, распакуйте его и загрузите файл из архива; "
            "таблицу .ods сохраните как .xlsx (Книга Excel).",
        )
    known = ", ".join(sorted({f for r in readers for f in r.formats})) or "нет читателей"
    raise AgenError(ErrorCode.FILE_FORMAT, f"Не знаю, как читать {path.name}. Поддерживаются: {known}")


def read_options(
    path: str | Path,
    registry: PluginRegistry,
    options: ReadOptions | None = None,
    fmt: str | None = None,
) -> tuple[ReaderPlugin, ReadOptions]:
    """Читатель и параметры чтения файла (заданные плюс найденные)."""
    p = Path(path)
    reader = choose_reader(p, registry, fmt)
    return reader, reader.sniff(p, options or ReadOptions())


CODE_WORDS = ("инн", "кпп", "огрн", "бик", "окпо", "код", "номер", "телефон", "артикул", "штрихкод", "id")


def _looks_like_code(name: str) -> bool:
    words = re.findall(r"[a-zа-яё0-9]+", name.lower())
    return "№" in name or any(w.startswith(c) for w in words for c in CODE_WORDS)


def _cell(v: object) -> str | None:
    return None if v is None else str(v)


def inspect_file(
    path: str | Path,
    registry: PluginRegistry,
    options: ReadOptions | None = None,
    fmt: str | None = None,
    sample_rows: int = SAMPLE_ROWS,
    profile: bool = True,
) -> SchemaSnapshot:
    """Снимок структуры: параметры чтения, названия столбцов, типы, выведенные по выборке
    из начала, середины и конца файла, профиль по выборке и первые строки (F-104…F-106)."""
    p = Path(path)
    reader, opts = read_options(p, registry, options, fmt)
    sample = reader.sample(p, opts, rows=sample_rows)
    df = pl.from_arrow(sample.table)
    assert isinstance(df, pl.DataFrame)
    if df.width == 0:
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {p.name} нет столбцов")
    df = df.cast(pl.String)
    df = df.filter(~pl.all_horizontal(pl.all().is_null()))
    if df.height == 0:
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {p.name} нет строк с данными")
    columns = []
    typed: dict[str, pl.Series] = {}
    for name in df.columns:
        s = df.get_column(name)
        dtype, fmt_found, share = infer_dtype_share(s)
        if dtype == DType.INT and _looks_like_code(name):
            # ИНН, коды, номера и телефоны — текст: с ними не считают, а ведущие нули важны.
            dtype, share = DType.STRING, None
        sample_values = [str(v) for v in s.drop_nulls().unique(maintain_order=True).head(5).to_list()]
        columns.append(
            ColumnSnapshot(
                source_name=name,
                dtype=dtype,
                format=fmt_found,
                non_null=s.len() - s.null_count(),
                sample=sample_values,
                parsed_share=share,
            )
        )
        typed[name] = df.select(cast_expr(pl.col(name), dtype)).to_series()
    if profile:
        profiles = profile_frame(pl.DataFrame(typed), exact=False)
        for c in columns:
            c.profile = profiles.get(c.source_name)
    preview = [[_cell(v) for v in row] for row in df.head(PREVIEW_ROWS).iter_rows()]
    size = p.stat().st_size
    return SchemaSnapshot(
        path=str(p),
        format=reader.name,
        options=opts,
        columns=columns,
        sample_rows=df.height,
        sample_parts=sample.parts,
        sheets=[str(s) for s in opts.sheets or []],
        file_size=size,
        rows_estimate=sample.rows_estimate,
        preview=preview,
        notes=sample.notes,
    )


def header_snapshot(
    path: str | Path,
    registry: PluginRegistry,
    options: ReadOptions | None = None,
    fmt: str | None = None,
) -> SchemaSnapshot:
    """Снимок только по шапке: названия столбцов и параметры чтения, без выборки и типов.
    Для сверки при загрузке этого достаточно; у Excel так не приходится читать лист дважды."""
    p = Path(path)
    reader, opts = read_options(p, registry, options, fmt)
    names = reader.columns(p, opts)
    if not names:
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {p.name} нет столбцов")
    return SchemaSnapshot(
        path=str(p),
        format=reader.name,
        options=opts,
        columns=[ColumnSnapshot(source_name=n, dtype=DType.STRING) for n in names],
        sample_rows=0,
        sheets=[str(s) for s in opts.sheets or []],
        file_size=p.stat().st_size,
        notes=["Снимок по шапке: типы столбцов — из источника"],
    )
