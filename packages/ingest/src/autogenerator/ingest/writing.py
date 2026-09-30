"""Запись выгрузки в Parquet, разложенный по месяцам столбца периода.

Столбцы переименовываются в ``id`` источника, приводятся к его типам, добавляются служебные
``_upload_id``, ``_upload_seq`` и ``_row`` (ARCHITECTURE.md, разделы 6.1 и 6.3). Файл
читается порциями, каждая порция сразу пишется на диск. Загрузка записывается во временную
папку и переименовывается целиком, поэтому после сбоя не остаётся половины загрузки.
"""

from __future__ import annotations

import os
import shutil
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
    ReadOptions,
    SourceSpec,
    UploadResult,
    UploadStatus,
)
from autogenerator.plugin_host import PluginRegistry

from .casting import POLARS_TYPES, cast_expr
from .reading import choose_reader

MAX_EXAMPLES = 20
# Доля ошибок приведения в используемом столбце, после которой загрузка идёт на проверку.
REVIEW_ERROR_SHARE = 0.001
NULL_MONTH = "none"


@dataclass
class _ColumnStats:
    dtype_name: str
    errors: int = 0
    examples: list[str] = field(default_factory=list)


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
    batch_rows: int = 100_000,
) -> UploadResult:
    """Прочитать файл и записать загрузку.

    ``mapping`` — название в файле → id столбца (результат сверки ``schema``). Столбцы
    источника, которых нет в файле, становятся пустыми. ``required`` — id столбцов, которые
    использует сценарий: ошибки приведения в них отправляют загрузку на проверку.
    """
    p = Path(path)
    out = Path(out_dir)
    if out.exists():
        raise AgenError(ErrorCode.SPEC_INVALID, f"Загрузка {upload_id} уже записана: {out}")
    tmp = out.with_name(out.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    reader = choose_reader(p, registry, source.format)
    opts = reader.sniff(p, (options or source.options))
    by_id = {v: k for k, v in mapping.items()}
    period = source.period_column
    required = set(required or ()) | {period}

    stats = {c.id: _ColumnStats(c.dtype.value) for c in source.columns if c.id in by_id}
    writers: dict[str, pq.ParquetWriter] = {}
    rejects: pq.ParquetWriter | None = None
    rows = 0
    null_period = 0
    pmin: date | None = None
    pmax: date | None = None

    try:
        for batch in reader.batches(p, opts, batch_rows=batch_rows):
            raw = pl.from_arrow(batch)
            assert isinstance(raw, pl.DataFrame)
            n = raw.height
            if n == 0:
                continue
            exprs = []
            for col in source.columns:
                fname = by_id.get(col.id)
                if fname is None or fname not in raw.columns:
                    exprs.append(pl.lit(None, dtype=POLARS_TYPES[col.dtype]).alias(col.id))
                else:
                    exprs.append(cast_expr(pl.col(fname), col.dtype).alias(col.id))
            typed = raw.select(exprs)

            # Ошибки приведения: было непустое значение, стало пустым.
            bad_any = pl.Series("bad", [False] * n)
            for cid, st in stats.items():
                fname = by_id[cid]
                src = raw.get_column(fname)
                bad = src.is_not_null() & (src.str.strip_chars() != "") & typed.get_column(cid).is_null()
                cnt = int(bad.sum())
                if cnt:
                    st.errors += cnt
                    bad_any = bad_any | bad
                    if len(st.examples) < MAX_EXAMPLES:
                        for v in src.filter(bad).unique(maintain_order=True).to_list():
                            if v not in st.examples and len(st.examples) < MAX_EXAMPLES:
                                st.examples.append(v)

            row_numbers = pl.int_range(rows + 1, rows + n + 1, eager=True, dtype=pl.Int64)
            if bool(bad_any.any()):
                rej = raw.with_columns(row_numbers.alias("_row")).filter(bad_any)
                rej_table = rej.to_arrow()
                if rejects is None:
                    rejects = pq.ParquetWriter(tmp / "rejects.parquet", rej_table.schema, compression="zstd")
                rejects.write_table(rej_table.cast(rejects.schema))

            typed = typed.with_columns(
                pl.lit(upload_id).alias("_upload_id"),
                pl.lit(upload_seq, dtype=pl.Int32).alias("_upload_seq"),
                row_numbers.alias("_row"),
            )
            pser = typed.get_column(period)
            null_period += pser.null_count()
            bmin, bmax = _as_date(pser.min()), _as_date(pser.max())
            if bmin is not None and bmax is not None:
                pmin = bmin if pmin is None else min(pmin, bmin)
                pmax = bmax if pmax is None else max(pmax, bmax)

            months = pser.dt.strftime("%Y-%m").fill_null(NULL_MONTH).alias("_month")
            for (month,), part in (
                typed.with_columns(months)
                .partition_by("_month", as_dict=True, include_key=False, maintain_order=True)
                .items()
            ):
                table = part.to_arrow()
                w = writers.get(str(month))
                if w is None:
                    d = tmp / f"month={month}"
                    d.mkdir()
                    w = pq.ParquetWriter(d / "part-0.parquet", table.schema, compression="zstd")
                    writers[str(month)] = w
                w.write_table(table.cast(w.schema))
            rows += n
    except BaseException:
        for w in writers.values():
            w.close()
        if rejects is not None:
            rejects.close()
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    for w in writers.values():
        w.close()
    if rejects is not None:
        rejects.close()
    if rows == 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise AgenError(ErrorCode.FILE_FORMAT, f"В файле {p.name} нет строк с данными")
    os.replace(tmp, out)

    issues = [
        CastIssue(column=cid, dtype=source.column(cid).dtype, errors=st.errors, examples=st.examples)
        for cid, st in stats.items()
        if st.errors
    ]
    reasons = []
    for ci in issues:
        share = ci.errors / rows
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
        rejects_uri=(out / "rejects.parquet").as_posix() if rejects is not None else None,
        rows=rows,
        months=sorted(writers),
        null_period_rows=null_period,
        period_min=pmin,
        period_max=pmax,
        cast_issues=issues,
        status=UploadStatus.NEEDS_REVIEW if reasons else UploadStatus.ACTIVE,
        review_reasons=reasons,
    )


def _as_date(v: object) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def read_upload_table(data_uri: str) -> pa.Table:
    """Вся загрузка одной таблицей — для отладки и тестов на маленьких файлах."""
    return pl.read_parquet(f"{data_uri}/month=*/*.parquet", hive_partitioning=False).to_arrow()
