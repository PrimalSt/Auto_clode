import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from autogenerator.contracts import AgenError, DType, ErrorCode, SourceSpec, UploadStatus
from autogenerator.ingest import (
    FilePart,
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


def test_office_document_that_no_reader_opens(tmp_path: Path, registry):
    # Подпись двоичного документа Office (OLE2): книгу .xls узнал бы читатель, а это
    # документ Word, письмо Outlook или книга с паролем.
    f = tmp_path / "a.xls"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)
    with pytest.raises(AgenError, match="не читается как книга Excel") as e:
        inspect_file(f, registry)
    assert "пароль" in (e.value.hint or "")


def test_zip_without_excel_book(tmp_path: Path, registry):
    # Книги .xlsx и .xlsb — zip, их узнал бы читатель; а в этом zip книги нет (документ Word).
    f = tmp_path / "a.xlsb"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("word/document.xml", "<document/>")
    with pytest.raises(AgenError, match="не читается как книга Excel: это zip-архив без книги") as e:
        inspect_file(f, registry)
    assert "распакуйте" in (e.value.hint or "")


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


def test_parts_of_one_export_become_one_upload(tmp_path: Path, registry, jsonl):
    """Выгрузка из двух файлов: столбцы во втором в другом порядке, строки нумеруются
    сквозь оба файла, строки с ошибками — по файлу в папке rejects."""
    a = jsonl(tmp_path / "a.jsonl", ["Дата", "Сумма"], [["01.03.2026", "1"], ["02.03.2026", "2"]])
    b = jsonl(tmp_path / "b.jsonl", ["Сумма", "Дата"], [["3", "03.03.2026"], ["сто", "04.03.2026"]])
    res = write_upload(
        a,
        registry,
        source=SOURCE,
        mapping={"Дата": "date", "Сумма": "amount"},
        upload_id="u",
        upload_seq=1,
        out_dir=tmp_path / "up",
        more=[FilePart(b, {"Сумма": "amount", "Дата": "date"})],
    )
    t = pl.from_arrow(read_upload_table(res.data_uri))
    assert t["_row"].to_list() == [1, 2, 3, 4]
    assert t["amount"].to_list() == [1.0, 2.0, 3.0, None]
    assert (res.rows, res.period_min, res.period_max) == (4, date(2026, 3, 1), date(2026, 3, 4))
    rejects = pl.read_parquet(res.rejects_uri)
    assert res.rejects_uri.endswith("/rejects")
    assert rejects["_file"].to_list() == ["b.jsonl"] and rejects["_row"].to_list() == [4]


def test_reader_notes_go_to_result(tmp_path: Path, registry, jsonl):
    """Замечания читателя (например, отброшенные лишние поля) не теряются: они в итоге
    записи, по файлу выгрузки."""
    a = jsonl(tmp_path / "a.jsonl", ["Дата", "Сумма"], [["01.03.2026", "1", "лишнее"], ["02.03.2026", "2"]])
    b = jsonl(tmp_path / "b.jsonl", ["Дата", "Сумма"], [["03.03.2026", "3"]])
    res = write_upload(
        a,
        registry,
        source=SOURCE,
        mapping={"Дата": "date", "Сумма": "amount"},
        upload_id="u",
        upload_seq=1,
        out_dir=tmp_path / "up",
        more=[FilePart(b, {"Дата": "date", "Сумма": "amount"})],
    )
    assert res.rows == 3 and res.notes == ["a.jsonl: строк длиннее шапки — 1"]


def test_fixed_period_for_snapshot_exports(tmp_path: Path, registry, jsonl):
    source = SourceSpec(
        id="clients",
        name="Клиенты",
        period_column="period",
        period_from="upload",
        columns=[{"id": "period", "name": "Период загрузки", "dtype": "date"}, {"id": "client", "name": "Клиент"}],
    )
    f = jsonl(tmp_path / "a.jsonl", ["Клиент"], [["a"], ["b"]])
    res = write_upload(
        f,
        registry,
        source=source,
        mapping={"Клиент": "client"},
        upload_id="u",
        upload_seq=1,
        out_dir=tmp_path / "up",
        fixed_period=date(2026, 1, 1),
    )
    assert res.months == ["2026-01"] and res.period_min == res.period_max == date(2026, 1, 1)
    assert pl.from_arrow(read_upload_table(res.data_uri))["period"].to_list() == [date(2026, 1, 1)] * 2
