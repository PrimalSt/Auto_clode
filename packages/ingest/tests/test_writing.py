from datetime import date
from pathlib import Path

import polars as pl
import pytest

from autogenerator.contracts import AgenError, DType, ErrorCode, SourceSpec, UploadStatus
from autogenerator.ingest import (
    header_snapshot,
    inspect_file,
    profile_frame,
    profile_upload,
    read_upload_table,
    write_upload,
)

SOURCE = SourceSpec(
    id="sales",
    name="Продажи",
    period_column="date",
    columns=[
        {"id": "date", "name": "Дата", "dtype": "date"},
        {"id": "amount", "name": "Сумма", "dtype": "float"},
        {"id": "region", "name": "Регион", "dtype": "string"},
        {"id": "channel", "name": "Канал", "dtype": "string"},
    ],
)
MAPPING = {"Дата": "date", "Сумма": "amount", "Регион": "region"}


def test_inspect(tmp_path: Path, registry, jsonl):
    f = jsonl(tmp_path / "a.jsonl", ["Дата", "Сумма"], [["01.01.2026", "1 000,5"], ["02.01.2026", "7"]])
    snap = inspect_file(f, registry)
    assert snap.format == "jsonl"
    assert [(c.source_name, c.dtype) for c in snap.columns] == [
        ("Дата", DType.DATE),
        ("Сумма", DType.FLOAT),
    ]
    assert snap.columns[1].sample == ["1 000,5", "7"]


def test_unknown_format(tmp_path: Path, registry, jsonl):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(AgenError, match="Не знаю, как читать"):
        inspect_file(f, registry)


def test_write_partitions_by_month(tmp_path: Path, registry, jsonl):
    f = jsonl(
        tmp_path / "a.jsonl",
        ["Сумма", "Регион", "Дата"],
        [["10", "Москва", "31.01.2026"], ["20", "Казань", "01.02.2026"], ["5", "Казань", None]],
    )
    out = tmp_path / "up1"
    res = write_upload(
        f,
        registry,
        source=SOURCE,
        mapping=MAPPING,
        upload_id="u1",
        upload_seq=1,
        out_dir=out,
        batch_rows=2,
    )
    assert res.status == UploadStatus.ACTIVE
    assert res.months == ["2026-01", "2026-02", "none"]
    assert (res.period_min, res.period_max, res.null_period_rows) == (
        date(2026, 1, 31),
        date(2026, 2, 1),
        1,
    )
    assert sorted(p.name for p in out.iterdir()) == ["month=2026-01", "month=2026-02", "month=none"]
    df = pl.from_arrow(read_upload_table(res.data_uri)).sort("_row")
    assert df.columns == [
        "date",
        "amount",
        "region",
        "channel",
        "_upload_id",
        "_upload_seq",
        "_row",
    ]
    assert df["_row"].to_list() == [1, 2, 3]
    assert df["amount"].to_list() == [10.0, 20.0, 5.0]
    # Столбца «Канал» в файле нет — он пустой, но в схеме есть.
    assert df["channel"].null_count() == 3


def test_cast_errors_go_to_rejects_and_review(tmp_path: Path, registry, jsonl):
    rows = [[f"{d:02d}.03.2026", str(d)] for d in range(1, 29)]
    rows[3][1] = "сто"
    rows[5][0] = "32.03.2026"
    f = jsonl(tmp_path / "a.jsonl", ["Дата", "Сумма"], rows)
    res = write_upload(
        f,
        registry,
        source=SOURCE,
        mapping={"Дата": "date", "Сумма": "amount"},
        upload_id="u",
        upload_seq=1,
        out_dir=tmp_path / "up",
        required={"amount"},
    )
    assert res.status == UploadStatus.NEEDS_REVIEW
    assert {c.column: c.examples for c in res.cast_issues} == {
        "date": ["32.03.2026"],
        "amount": ["сто"],
    }
    assert len(res.review_reasons) == 2
    rejects = pl.read_parquet(res.rejects_uri)
    assert rejects["_row"].to_list() == [4, 6]
    # Строки с ошибками остаются в загрузке (со значением null): решение — за пользователем.
    assert res.rows == 28


def test_existing_upload_is_not_overwritten(tmp_path: Path, registry, jsonl):
    f = jsonl(tmp_path / "a.jsonl", ["Дата"], [["01.01.2026"]])
    kw = dict(source=SOURCE, mapping={"Дата": "date"}, upload_seq=1, out_dir=tmp_path / "up")
    write_upload(f, registry, upload_id="u", **kw)
    with pytest.raises(AgenError, match="уже записана"):
        write_upload(f, registry, upload_id="u", **kw)


def test_failed_write_leaves_nothing(tmp_path: Path, registry, jsonl):
    f = jsonl(tmp_path / "a.jsonl", ["Дата"], [])
    with pytest.raises(AgenError, match="нет строк"):
        write_upload(
            f,
            registry,
            source=SOURCE,
            mapping={"Дата": "date"},
            upload_id="u",
            upload_seq=1,
            out_dir=tmp_path / "up",
        )
    assert list(tmp_path.iterdir()) == [f]


def test_inspect_profile_and_header_snapshot(tmp_path: Path, registry, jsonl):
    rows = [[f"{d:02d}.01.2026", "Москва" if d % 3 else "Казань", None if d == 5 else str(d)] for d in range(1, 31)]
    f = jsonl(tmp_path / "a.jsonl", ["Дата", "Регион", "Сумма"], rows)
    snap = inspect_file(f, registry)
    prof = {c.source_name: c.profile for c in snap.columns}
    assert prof["Дата"].min == "2026-01-01" and prof["Дата"].max == "2026-01-30"
    assert prof["Регион"].unique == 2 and prof["Регион"].top[0].value == "Москва"
    assert prof["Сумма"].nulls == 1 and not prof["Сумма"].exact
    assert snap.file_size > 0 and snap.preview[0] == ["01.01.2026", "Москва", "1"]
    head = header_snapshot(f, registry)
    assert head.names() == ["Дата", "Регион", "Сумма"] and head.sample_rows == 0


def test_profile_frame_skips_top_for_unique_columns():
    df = pl.DataFrame({"id": [str(i) for i in range(50)], "k": ["a", "b"] * 25})
    prof = profile_frame(df)
    assert prof["id"].top_skipped and prof["id"].top == []
    assert [(t.value, t.count) for t in prof["k"].top] == [("a", 25), ("b", 25)]


def test_exact_profile_of_written_upload(tmp_path: Path, registry, jsonl):
    f = jsonl(tmp_path / "a.jsonl", ["Дата", "Сумма"], [["01.01.2026", "1"], ["03.02.2026", "2"], [None, "3"]])
    res = write_upload(
        f,
        registry,
        source=SOURCE,
        mapping={"Дата": "date", "Сумма": "amount"},
        upload_id="u",
        upload_seq=1,
        out_dir=tmp_path / "up",
    )
    prof = profile_upload(res.data_uri, ["date", "amount", "channel"])
    assert (prof["date"].min, prof["date"].max, prof["date"].nulls) == ("2026-01-01", "2026-02-03", 1)
    assert prof["amount"].exact and prof["amount"].rows == 3
    assert prof["channel"].nulls == 3


def test_blank_rows_are_skipped(tmp_path: Path, registry, jsonl):
    f = jsonl(tmp_path / "a.jsonl", ["Дата", "Сумма"], [["01.01.2026", "1"], [None, None], ["02.01.2026", "2"]])
    res = write_upload(
        f,
        registry,
        source=SOURCE,
        mapping={"Дата": "date", "Сумма": "amount"},
        upload_id="u",
        upload_seq=1,
        out_dir=tmp_path / "up",
    )
    assert (res.rows, res.empty_rows, res.null_period_rows) == (2, 1, 0)
    # Номера строк — как в файле: пустая вторая строка пропущена, третья осталась третьей.
    assert pl.from_arrow(read_upload_table(res.data_uri))["_row"].to_list() == [1, 3]


def test_progress_and_cancel(tmp_path: Path, registry, jsonl):
    f = jsonl(tmp_path / "a.jsonl", ["Дата"], [[f"{d:02d}.01.2026"] for d in range(1, 31)])
    events = []
    kw = dict(source=SOURCE, mapping={"Дата": "date"}, upload_id="u", upload_seq=1, batch_rows=10)
    write_upload(f, registry, out_dir=tmp_path / "ok", progress=events.append, **kw)
    assert [e.done for e in events if e.stage == "запись"] == [10, 20, 30]
    calls = iter(range(100))
    with pytest.raises(AgenError) as err:
        write_upload(f, registry, out_dir=tmp_path / "cancel", cancelled=lambda: next(calls) >= 1, **kw)
    assert err.value.code == ErrorCode.CANCELLED
    # После отмены не остаётся ни загрузки, ни временной папки.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jsonl", "ok"]
