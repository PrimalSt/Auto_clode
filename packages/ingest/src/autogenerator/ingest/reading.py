"""Выбор читателя и снимок структуры файла."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from autogenerator.contracts import (
    AgenError,
    ColumnSnapshot,
    ErrorCode,
    PluginKind,
    ReaderPlugin,
    ReadOptions,
    SchemaSnapshot,
)
from autogenerator.plugin_host import PluginRegistry

from .casting import infer_dtype

SAMPLE_ROWS = 10_000


def choose_reader(path: Path, registry: PluginRegistry, fmt: str | None = None) -> ReaderPlugin:
    """Читатель по явному формату или по файлу: сначала те, чьё расширение совпало."""
    if not path.exists():
        raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {path}")
    if fmt:
        return registry.reader(fmt)
    readers: list[ReaderPlugin] = registry.all(PluginKind.READER)
    suffix = path.suffix.lower().lstrip(".")
    ordered = [r for r in readers if suffix in r.formats] + [r for r in readers if suffix not in r.formats]
    for r in ordered:
        try:
            if r.can_read(path):
                return r
        except Exception:
            continue
    known = ", ".join(sorted({f for r in readers for f in r.formats})) or "нет читателей"
    raise AgenError(ErrorCode.FILE_FORMAT, f"Не знаю, как читать {path.name}. Поддерживаются: {known}")


def inspect_file(
    path: str | Path,
    registry: PluginRegistry,
    options: ReadOptions | None = None,
    fmt: str | None = None,
    sample_rows: int = SAMPLE_ROWS,
) -> SchemaSnapshot:
    """Снимок структуры: названия столбцов, выведенные типы и примеры значений.

    На этапе M0 типы выводятся по первым ``sample_rows`` строкам; порции из середины и
    конца файла добавятся на этапе M1.
    """
    p = Path(path)
    reader = choose_reader(p, registry, fmt)
    opts = reader.sniff(p, options or ReadOptions())
    first = next(iter(reader.batches(p, opts, batch_rows=sample_rows)), None)
    if first is None or first.num_rows == 0:
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {p.name} нет строк с данными")
    df = pl.from_arrow(first)
    assert isinstance(df, pl.DataFrame)
    columns = []
    for name in df.columns:
        s = df.get_column(name)
        dtype, fmt_found = infer_dtype(s)
        sample = [str(v) for v in s.drop_nulls().unique(maintain_order=True).head(5).to_list()]
        columns.append(
            ColumnSnapshot(
                source_name=name,
                dtype=dtype,
                format=fmt_found,
                non_null=s.len() - s.null_count(),
                sample=sample,
            )
        )
    return SchemaSnapshot(path=str(p), format=reader.name, options=opts, columns=columns, sample_rows=df.height)
