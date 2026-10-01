import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    OverlapPolicy,
    Period,
    SourceSpec,
    UploadRecord,
    UploadStatus,
)
from autogenerator.storage import DataFolder, LocalBlobStore, SqliteMetadataStore, check_location, default_home

SPEC = SourceSpec(
    id="sales",
    name="Продажи",
    period_column="date",
    columns=[{"id": "date", "name": "Дата", "dtype": "date"}, {"id": "amount", "name": "Сумма", "dtype": "float"}],
)


def upload(seq: int, period: str = "2026-01", **kw) -> UploadRecord:
    data = dict(
        id=f"sales-{seq:03d}",
        source_id="sales",
        seq=seq,
        source_version=1,
        original_name=f"Продажи_{period}.csv",
        sha256=f"{seq:064d}",
        size=100,
        format="csv",
        data_uri=f"/data/sales-{seq:03d}",
        mapping={"Дата": "date"},
        period=Period.parse(period),
        rows=10,
        status=UploadStatus.ACTIVE,
        uploaded_at=datetime(2026, 2, 1, 12, tzinfo=UTC),
    )
    data.update(kw)
    return UploadRecord.model_validate(data)


@pytest.fixture
def folder(tmp_path: Path) -> DataFolder:
    return DataFolder.open(tmp_path / "home")


@pytest.fixture
def store(folder: DataFolder) -> SqliteMetadataStore:
    s = SqliteMetadataStore(folder.db_path, folder.backups)
    yield s
    s.close()


def test_default_home_and_layout(tmp_path: Path, monkeypatch, folder: DataFolder):
    monkeypatch.setenv("AGEN_HOME", str(tmp_path / "x"))
    assert default_home() == tmp_path / "x"
    assert {p.name for p in folder.root.iterdir()} >= {"db", "tmp", "backups", "cache", "logs"}
    assert folder.upload_dir("sales", "u1") == folder.root / "local" / "sources" / "sales" / "uploads" / "u1"


def test_location_checks(tmp_path: Path):
    assert check_location(tmp_path) == []
    assert check_location(tmp_path / "OneDrive - Компания" / "Autogenerator")
    with pytest.raises(AgenError) as e:
        check_location(Path("//server/share/Autogenerator"))
    assert e.value.code == ErrorCode.DATA_FOLDER


def test_lock_is_exclusive(folder: DataFolder):
    with folder.lock("первая команда"):
        with pytest.raises(AgenError, match="первая команда") as e:
            folder.lock().acquire()
        assert e.value.code == ErrorCode.DATA_FOLDER_LOCKED
    # После снятия блокировки папку можно взять снова.
    folder.lock().acquire().release()


def test_sources_and_versions(store: SqliteMetadataStore):
    rec = store.create_source(SPEC)
    assert (rec.version, rec.spec.version) == (1, 1)
    with pytest.raises(AgenError, match="уже есть"):
        store.create_source(SPEC)
    same = store.update_source(SPEC.model_copy(update={"version": 7}))
    assert same.version == 1  # без изменений новая версия не создаётся
    v2 = store.update_source(SPEC.model_copy(update={"overlap_policy": OverlapPolicy.APPEND}), "добавить строки")
    assert v2.version == 2 and v2.spec.overlap_policy == OverlapPolicy.APPEND
    assert [(v.number, v.comment) for v in store.source_versions("sales")] == [(1, "создан"), (2, "добавить строки")]
    with pytest.raises(AgenError) as e:
        store.get_source("nope")
    assert e.value.code == ErrorCode.NOT_FOUND


def test_uploads_round_trip_and_manifest(store: SqliteMetadataStore):
    store.create_source(SPEC)
    assert store.next_upload_seq("sales") == 1
    store.add_upload(upload(1, "2026-01", period_from_data=Period.parse("2026-01-09..2026-01-31")))
    store.add_upload(upload(2, "2026-02", status=UploadStatus.NEEDS_REVIEW, review_reasons=["ошибки"]))
    assert store.next_upload_seq("sales") == 3
    got = store.get_upload("sales-001")
    assert got == upload(1, "2026-01", period_from_data=Period.parse("2026-01-09..2026-01-31"))
    assert [u.id for u in store.find_uploads(f"{2:064d}")] == ["sales-002"]
    store.update_upload("sales-002", status=UploadStatus.ACTIVE, overlap_policy=OverlapPolicy.APPEND)
    m = store.history_manifest("sales")
    assert [(u.id, u.period.key, u.status, u.overlap_policy) for u in m.uploads] == [
        ("sales-001", "2026-01", UploadStatus.ACTIVE, None),
        ("sales-002", "2026-02", UploadStatus.ACTIVE, OverlapPolicy.APPEND),
    ]
    assert m.columns == {"date": "date", "amount": "float"} and m.source_version == 1
    store.delete_upload("sales-001")
    assert [u.seq for u in store.list_uploads("sales")] == [2]
    store.delete_source("sales")
    assert store.list_sources() == []


def test_migrations_and_backup(folder: DataFolder):
    s = SqliteMetadataStore(folder.db_path, folder.backups)
    assert s.schema_revision() == "0001"
    s.create_source(SPEC)
    s.close()
    # Повторное открытие той же версии схемы ничего не мигрирует и не копирует.
    s = SqliteMetadataStore(folder.db_path, folder.backups)
    assert list(folder.backups.iterdir()) == []
    copy = s.backup(folder.backups)
    s.close()
    with sqlite3.connect(copy) as db:
        assert db.execute("select id from sources").fetchall() == [("sales",)]
    with sqlite3.connect(folder.db_path) as db:
        assert db.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_blob_store(folder: DataFolder, tmp_path: Path):
    blobs = LocalBlobStore(folder)
    src = tmp_path / "a.csv"
    src.write_text("abc", encoding="utf-8")
    uri = blobs.upload_uri("sales", "u1") + "/original.csv"
    blobs.put_file(str(src), uri)
    assert Path(uri).read_text(encoding="utf-8") == "abc"
    assert blobs.size(blobs.upload_uri("sales", "u1")) == 3
    blobs.delete(blobs.upload_uri("sales", "u1"))
    assert not Path(uri).exists()
    assert Path(blobs.tmp_uri()).parent == folder.tmp
    assert blobs.free_bytes() > 0
