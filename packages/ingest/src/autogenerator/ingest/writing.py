"""Запись выгрузки в Parquet, разложенный по месяцам столбца периода.

Столбцы переименовываются в ``id`` источника, приводятся к его типам, добавляются служебные
``_upload_id``, ``_upload_seq`` и ``_row`` (ARCHITECTURE.md, разделы 6.1 и 6.3). Файл
читается порциями, каждая порция сразу пишется на диск — в свою часть
``month=ГГГГ-ММ/part-N.parquet``. Чтение, приведение и запись идут одновременно в разных
потоках (``pipeline``). Загрузка записывается во временную папку и переименовывается
целиком, поэтому после сбоя или отмены не остаётся половины загрузки.

Выгрузка может состоять из нескольких файлов (части одной выгрузки): они читаются по
очереди и склеиваются в одну загрузку, сквозная нумерация строк ``_row`` продолжается.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from autogenerator.contracts import (
    AgenError,
    CastIssue,
    ErrorCode,
    ProgressCallback,
    ReadOptions,
    ReadProgress,
    SourceSpec,
    UploadResult,
    UploadStatus,
)
from autogenerator.plugin_host import PluginRegistry

from .casting import CastColumn, cast_frame
from .pipeline import BackgroundWriter, prefetch
from .reading import choose_reader

MAX_EXAMPLES = 20
MAX_FIRST_ROWS = 10
# Доля ошибок приведения в используемом столбце, после которой загрузка идёт на проверку.
REVIEW_ERROR_SHARE = 0.001
NULL_MONTH = "none"
BATCH_ROWS = 1_000_000
"""Читатели отдают порции по своим размерам (CSV — около 100–250 тыс. строк); больше
этого порцию не дробим."""


@dataclass
class _ColumnStats:
    errors: int = 0
    examples: list[str] = field(default_factory=list)
    first_rows: list[int] = field(default_factory=list)


def _replace_dir(tmp: Path, out: Path, attempts: int = 10) -> None:
    """Переименовать папку; антивирус может ненадолго держать только что записанный файл."""
    for i in range(attempts):
        try:
            os.replace(tmp, out)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


@dataclass
class FilePart:
    """Ещё один файл той же выгрузки: путь и сопоставление его столбцов (название → id)."""

    path: Path
    mapping: dict[str, str]
    options: ReadOptions | None = None


def write_upload(
    path: str | Path,
    registry: PluginRegistry,
    *,
    source: SourceSpec,
    mapping: dict[str, str],
    upload_id: str,
    upload_seq: int,
    out_dir: str | Path,
    options: ReadOptions | None = None,
    required: set[str] | None = None,
    batch_rows: int = BATCH_ROWS,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
    more: Sequence[FilePart] = (),
    fixed_period: date | None = None,
) -> UploadResult:
    """Прочитать файл и записать загрузку.

    ``mapping`` — название в файле → id столбца (результат сверки ``schema``). Столбцы
    источника, которых нет в файле, становятся пустыми. ``required`` — id столбцов, которые
    использует сценарий: ошибки приведения в них отправляют загрузку на проверку.
    ``cancelled`` опрашивается между порциями: отмена удаляет недописанную загрузку.
    ``more`` — следующие файлы этой же выгрузки, они дописываются в ту же загрузку.
    ``fixed_period`` — значение столбца периода для всех строк, когда период задаётся при
    загрузке (выгрузка-срез без столбца с месяцем).
    """
    t0 = time.perf_counter()
    parts = [FilePart(Path(path), mapping, options), *more]
    out = Path(out_dir)
    if out.exists():
        raise AgenError(ErrorCode.ALREADY_EXISTS, f"Загрузка {upload_id} уже записана: {out}")
    tmp = out.with_name(out.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    period = source.period_column
    required = set(required or ()) | {period}
    stats: dict[str, _ColumnStats] = {}
    part_no: dict[str, int] = {}
    months: set[str] = set()
    rejects: pq.ParquetWriter | None = None
    rejects_path = tmp / "rejects.parquet" if len(parts) == 1 else tmp / "rejects"
    first_opts: ReadOptions | None = None
    rows = 0
    empty_rows = 0
    null_period = 0
    pmin: date | None = None
    pmax: date | None = None
    writer = BackgroundWriter(depth=2)

    def emit(stage: str, done: int, total: int | None, unit: str) -> None:
        if progress is not None:
            progress(ReadProgress(stage, done, total, unit))

    def write_part(frame: pl.DataFrame, target: Path) -> Callable[[], None]:
        def task() -> None:
            target.parent.mkdir(exist_ok=True)
            frame.write_parquet(target, compression="zstd", statistics=True)

        return task

    try:
        for i, part in enumerate(parts):
            reader = choose_reader(part.path, registry, source.format)
            opts = reader.sniff(part.path, (part.options or ReadOptions()).merged(source.options))
            first_opts = first_opts or opts
            by_id = {v: k for k, v in part.mapping.items()}
            plan = [CastColumn(c.id, c.dtype, by_id.get(c.id), c.format) for c in source.columns]
            for c in plan:
                if c.source is not None:
                    stats.setdefault(c.id, _ColumnStats())
            if rejects is not None and len(parts) > 1:
                rejects.close()
                rejects = None
            batches = reader.batches(part.path, opts, batch_rows=batch_rows, progress=progress)
            for batch in prefetch(iter(batches), depth=2):
                if cancelled is not None and cancelled():
                    raise AgenError(ErrorCode.CANCELLED, f"Загрузка {part.path.name} отменена")
                raw = pl.from_arrow(batch)
                assert isinstance(raw, pl.DataFrame)
                n = raw.height
                if n == 0:
                    continue
                raw = raw.with_columns(pl.int_range(rows + 1, rows + n + 1, dtype=pl.Int64).alias("_row"))
                rows += n
                # Пустые строки файла (в том числе «;;;;») — не данные.
                blank = raw.select(pl.all_horizontal(pl.exclude("_row").is_null()).alias("b")).get_column("b")
                if blank.any():
                    empty_rows += int(blank.sum())
                    raw = raw.filter(~blank)
                    if raw.height == 0:
                        continue

                outcome = cast_frame(raw, plan)
                typed = outcome.frame
                if outcome.bad:
                    bad_any = pl.Series("bad", [False] * raw.height)
                    for cid, mask in outcome.bad.items():
                        st = stats[cid]
                        bad_rows = raw.get_column("_row").filter(mask)
                        st.errors += bad_rows.len()
                        bad_any = bad_any | mask
                        if len(st.first_rows) < MAX_FIRST_ROWS:
                            st.first_rows += bad_rows.head(MAX_FIRST_ROWS - len(st.first_rows)).to_list()
                        if len(st.examples) < MAX_EXAMPLES:
                            src = raw.get_column(by_id[cid]).filter(mask)
                            for v in src.unique(maintain_order=True).head(MAX_EXAMPLES).to_list():
                                if v not in st.examples and len(st.examples) < MAX_EXAMPLES:
                                    st.examples.append(v)
                    bad = raw.filter(bad_any)
                    if len(parts) > 1:
                        bad = bad.with_columns(pl.lit(part.path.name).alias("_file"))
                    rej_table: pa.Table = bad.to_arrow()
                    if rejects is None:
                        rej_schema = pa.schema([pa.field(f.name, pa.large_string()) for f in rej_table.schema])
                        rej_schema = rej_schema.set(rej_schema.get_field_index("_row"), pa.field("_row", pa.int64()))
                        target = rejects_path if len(parts) == 1 else rejects_path / f"{i + 1}.parquet"
                        target.parent.mkdir(exist_ok=True)
                        rejects = pq.ParquetWriter(target, rej_schema, compression="zstd")
                    rejects.write_table(rej_table.cast(rejects.schema))

                if fixed_period is not None:
                    typed = typed.with_columns(pl.lit(fixed_period).cast(typed.schema[period]).alias(period))
                typed = typed.with_columns(
                    pl.lit(upload_id).alias("_upload_id"),
                    pl.lit(upload_seq, dtype=pl.Int32).alias("_upload_seq"),
                    raw.get_column("_row"),
                )
                pser = typed.get_column(period)
                null_period += pser.null_count()
                bmin, bmax = _as_date(pser.min()), _as_date(pser.max())
                if bmin is not None and bmax is not None:
                    pmin = bmin if pmin is None else min(pmin, bmin)
                    pmax = bmax if pmax is None else max(pmax, bmax)

                for month, chunk in _by_month(typed, period, bmin, bmax):
                    months.add(month)
                    k = part_no.get(month, 0)
                    part_no[month] = k + 1
                    writer.submit(write_part(chunk, tmp / f"month={month}" / f"part-{k}.parquet"))
                emit("запись", rows, None, "rows")
        writer.close()
    except BaseException:
        writer.abort()
        if rejects is not None:
            rejects.close()
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    if rejects is not None:
        rejects.close()
    assert first_opts is not None
    data_rows = rows - empty_rows
    if data_rows == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        names = ", ".join(x.path.name for x in parts)
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {names} нет строк с данными")
    written = sum(f.stat().st_size for f in tmp.rglob("*.parquet"))
    has_rejects = rejects_path.exists()
    _replace_dir(tmp, out)

    issues = [
        CastIssue(
            column=cid,
            dtype=source.column(cid).dtype,
            errors=st.errors,
            examples=st.examples,
            first_rows=st.first_rows,
        )
        for cid, st in stats.items()
        if st.errors
    ]
    reasons = []
    for ci in issues:
        share = ci.errors / data_rows
        if ci.column == period:
            reasons.append(
                f"В столбце периода «{ci.column}» не распознаны даты: {ci.errors} строк, "
                f"например {', '.join(ci.examples[:3])}"
            )
        elif ci.column in required and share > REVIEW_ERROR_SHARE:
            reasons.append(
                f"В столбце «{ci.column}» ({ci.dtype}) не распознано {ci.errors} значений "
                f"({share:.2%}), например {', '.join(ci.examples[:3])}"
            )
    return UploadResult(
        upload_id=upload_id,
        upload_seq=upload_seq,
        data_uri=out.as_posix(),
        rejects_uri=(out / rejects_path.name).as_posix() if has_rejects else None,
        rows=data_rows,
        empty_rows=empty_rows,
        months=sorted(months),
        null_period_rows=null_period,
        period_min=pmin,
        period_max=pmax,
        cast_issues=issues,
        status=UploadStatus.NEEDS_REVIEW if reasons else UploadStatus.ACTIVE,
        review_reasons=reasons,
        options=first_opts,
        sheets=[str(s) for s in first_opts.sheets or []],
        bytes_written=written,
        seconds=round(time.perf_counter() - t0, 2),
    )


def _by_month(typed: pl.DataFrame, period: str, bmin: date | None, bmax: date | None) -> list[tuple[str, pl.DataFrame]]:
    """Порция по месяцам столбца периода. Месячная выгрузка обычно целиком в одном месяце —
    тогда порция не делится."""
    pser = typed.get_column(period)
    same_month = bmin is not None and bmax is not None and (bmin.year, bmin.month) == (bmax.year, bmax.month)
    if same_month and bmin is not None and pser.null_count() == 0:
        return [(f"{bmin.year}-{bmin.month:02d}", typed)]
    key = (pser.dt.year() * 100 + pser.dt.month()).alias("_m")
    out = []
    for (m,), part in typed.with_columns(key).partition_by("_m", as_dict=True, include_key=False).items():
        name = NULL_MONTH if m is None else f"{int(m) // 100}-{int(m) % 100:02d}"
        out.append((name, part))
    return sorted(out, key=lambda x: x[0])


def _as_date(v: object) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def read_upload_table(data_uri: str) -> pa.Table:
    """Вся загрузка одной таблицей — для отладки и тестов на маленьких файлах."""
    files = sorted(str(f) for f in Path(data_uri).glob("month=*/*.parquet"))
    return pl.read_parquet(files, hive_partitioning=False).sort("_row").to_arrow()
