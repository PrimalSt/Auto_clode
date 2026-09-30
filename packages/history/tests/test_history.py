from datetime import date
from pathlib import Path

import polars as pl
import pytest

from autogenerator.contracts import (
    AgenError,
    DateSpan,
    HistoryManifest,
    OverlapPolicy,
    Period,
    PeriodUnit,
    UploadRef,
    UploadStatus,
)
from autogenerator.history import coverage, default_report_period, history_view, upload_period


def make_upload(root: Path, seq: int, rows: list[tuple[str, date | None, float]], period: str) -> UploadRef:
    """Загрузка в том виде, в каком её пишет ingest: Parquet по месяцам столбца периода."""
    uri = root / f"u{seq}"
    df = pl.DataFrame(
        {
            "key": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "amount": [r[2] for r in rows],
            "_upload_id": [f"u{seq}"] * len(rows),
            "_upload_seq": pl.Series([seq] * len(rows), dtype=pl.Int32),
            "_row": pl.Series(range(1, len(rows) + 1), dtype=pl.Int64),
        },
        schema_overrides={"date": pl.Date},
    )
    months = df["date"].dt.strftime("%Y-%m").fill_null("none")
    for (m,), part in df.with_columns(months.alias("_m")).partition_by("_m", as_dict=True, include_key=False).items():
        (uri / f"month={m}").mkdir(parents=True)
        part.write_parquet(uri / f"month={m}" / "part-0.parquet")
    return UploadRef(id=f"u{seq}", seq=seq, uri=str(uri), period=Period.parse(period), rows=len(rows))


def manifest(policy: OverlapPolicy, uploads: list[UploadRef], **kw) -> HistoryManifest:
    return HistoryManifest(
        source_id="s",
        period_column="date",
        period_type=PeriodUnit.MONTH,
        overlap_policy=policy,
        columns={"key": "string", "date": "date", "amount": "float"},
        uploads=uploads,
        **kw,
    )


def keys(m: HistoryManifest, **kw) -> list[tuple[str, float]]:
    df = history_view(m, **kw).collect().sort(["_upload_seq", "_row"])
    return list(zip(df["key"], df["amount"], strict=True))


@pytest.fixture
def uploads(tmp_path: Path) -> list[UploadRef]:
    jan = make_upload(
        tmp_path,
        1,
        [("a", date(2026, 1, 5), 1), ("b", date(2026, 1, 20), 2), ("x", None, 9)],
        "2026-01",
    )
    feb = make_upload(tmp_path, 2, [("c", date(2026, 2, 3), 3)], "2026-02")
    jan_fix = make_upload(tmp_path, 3, [("a", date(2026, 1, 5), 10)], "2026-01")
    return [jan, feb, jan_fix]


def test_replace_period(uploads):
    # Повторная выгрузка января заменяет январь; строка без даты старой загрузки остаётся.
    assert keys(manifest(OverlapPolicy.REPLACE_PERIOD, uploads)) == [("x", 9), ("c", 3), ("a", 10)]


def test_merge_dedupe(uploads):
    m = manifest(OverlapPolicy.MERGE_DEDUPE, uploads, keys=["key"])
    assert keys(m) == [("b", 2), ("x", 9), ("c", 3), ("a", 10)]
    with pytest.raises(AgenError, match="keys"):
        history_view(manifest(OverlapPolicy.MERGE_DEDUPE, uploads))


def test_append_and_replace_all(uploads):
    assert len(keys(manifest(OverlapPolicy.APPEND, uploads))) == 5
    assert keys(manifest(OverlapPolicy.REPLACE_ALL, uploads)) == [("a", 10)]


def test_ask_needs_ui(uploads):
    with pytest.raises(AgenError, match="ask"):
        history_view(manifest(OverlapPolicy.ASK, uploads))


def test_excluded_uploads_are_skipped(uploads):
    uploads[2].status = UploadStatus.EXCLUDED
    assert keys(manifest(OverlapPolicy.REPLACE_PERIOD, uploads)) == [
        ("a", 1),
        ("b", 2),
        ("x", 9),
        ("c", 3),
    ]


def test_bounds_and_columns(uploads):
    m = manifest(OverlapPolicy.APPEND, uploads)
    assert keys(m, lower=date(2026, 2, 1)) == [("c", 3)]
    assert keys(m, upper_exclusive=date(2026, 1, 10)) == [("a", 1), ("a", 10)]
    lf = history_view(m, columns=["amount"])
    assert lf.collect_schema().names() == ["date", "amount", "_upload_id", "_upload_seq", "_row"]


def test_column_added_later_is_null_in_old_uploads(uploads):
    m = manifest(OverlapPolicy.APPEND, uploads)
    m.columns["channel"] = "string"
    df = history_view(m).collect()
    assert df["channel"].null_count() == df.height


def test_upload_period():
    assert upload_period(date(2026, 1, 9), date(2026, 1, 31), PeriodUnit.MONTH) == Period.parse("2026-01")
    p = upload_period(date(2026, 1, 15), date(2026, 2, 10), PeriodUnit.MONTH)
    assert (p.unit, p.start, p.end_exclusive) == (
        PeriodUnit.RANGE,
        date(2026, 1, 1),
        date(2026, 3, 1),
    )
    assert upload_period(date(2026, 1, 1), date(2026, 3, 31), PeriodUnit.QUARTER) == Period.parse("2026-Q1")
    assert upload_period(date(2026, 1, 3), date(2026, 1, 9), PeriodUnit.RANGE).days == 7


def test_default_period_is_latest_end_not_latest_upload(uploads):
    # Дозагрузка января после февраля не сдвигает отчётный период назад.
    assert default_report_period(manifest(OverlapPolicy.REPLACE_PERIOD, uploads)).key == "2026-02"
    with pytest.raises(AgenError):
        default_report_period(manifest(OverlapPolicy.APPEND, []))


def test_default_period_of_multi_month_upload(tmp_path: Path):
    up = make_upload(tmp_path, 1, [("a", date(2026, 1, 5), 1)], "2026-01-01..2026-03-31")
    assert default_report_period(manifest(OverlapPolicy.APPEND, [up])).key == "2026-03"


def test_coverage_merges_adjacent(tmp_path: Path, uploads):
    apr = make_upload(tmp_path, 4, [("d", date(2026, 4, 1), 4)], "2026-04")
    cov = coverage(manifest(OverlapPolicy.APPEND, [*uploads, apr]))
    assert cov == [
        DateSpan(start=date(2026, 1, 1), end_exclusive=date(2026, 3, 1)),
        DateSpan(start=date(2026, 4, 1), end_exclusive=date(2026, 5, 1)),
    ]
