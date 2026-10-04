import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    HistoryManifest,
    Issue,
    OverlapPolicy,
    Period,
    RunRecord,
    RunResult,
    RunStatus,
    ScenarioSpec,
    SourceSpec,
    ThemeManifest,
    ThemeVersionRecord,
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
    # Какие столбцы нашлись в файле загрузки — по сопоставлению: «amount» в нём не было.
    assert m.uploads[0].file_columns == ["date"]
    store.delete_upload("sales-001")
    assert [u.seq for u in store.list_uploads("sales")] == [2]
    store.delete_source("sales")
    assert store.list_sources() == []


def test_migrations_and_backup(folder: DataFolder):
    s = SqliteMetadataStore(folder.db_path, folder.backups)
    assert s.schema_revision() == "0002"
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


def test_upgrade_from_m1_schema_makes_backup(folder: DataFolder):
    from alembic import command
    from sqlalchemy import create_engine

    from autogenerator.storage.metadata import _alembic_config

    engine = create_engine(f"sqlite:///{folder.db_path.as_posix()}")
    with engine.connect() as conn:
        command.upgrade(_alembic_config(conn), "0001")
        conn.commit()
    engine.dispose()
    s = SqliteMetadataStore(folder.db_path, folder.backups)
    assert s.schema_revision() == "0002"
    assert [p.name.endswith("before-0002.sqlite") for p in folder.backups.iterdir()] == [True]
    assert s.list_scenarios() == [] and s.list_themes() == [] and s.list_runs() == []
    s.close()


def scenario(name: str = "Продажи", source: str = "sales", **kw) -> ScenarioSpec:
    return ScenarioSpec.model_validate({"name": name, "inputs": [{"id": "s", "source": source, "main": True}], **kw})


def test_scenarios_and_versions(store: SqliteMetadataStore):
    store.create_source(SPEC)
    rec = store.save_scenario("sales_report", scenario(), text="name: Продажи\n", theme=("corp", 1))
    assert (rec.version, rec.name, rec.current.theme_id, rec.current.theme_version) == (1, "Продажи", "corp", 1)
    assert [(i.input_id, i.source_id, i.main) for i in rec.inputs] == [("s", "sales", True)]
    # То же самое — без новой версии; другое — новой версией.
    assert store.save_scenario("sales_report", scenario(), text="name: Продажи\n", theme=("corp", 1)).version == 1
    assert store.save_scenario("sales_report", scenario(), text="name: Продажи\n", theme=("corp", 2)).version == 2
    rec = store.save_scenario("sales_report", scenario("Продажи v3", source="plan"), comment="другой источник")
    assert (rec.version, rec.name, rec.current.text, rec.current.comment) == (3, "Продажи v3", None, "другой источник")
    assert [v.number for v in store.scenario_versions("sales_report")] == [1, 2, 3]
    assert store.get_scenario_version("sales_report", 1).spec.inputs[0].source == "sales"
    ref = store.scenarios_using_source("plan")[0]
    assert (ref.scenario_id, ref.input_id) == ("sales_report", "s")
    assert store.scenarios_using_source("sales") == []
    with pytest.raises(AgenError) as e:
        store.get_scenario("nope")
    assert e.value.code == ErrorCode.NOT_FOUND
    store.save_scenario("other", scenario(source="sales"))
    with pytest.raises(AgenError) as e:
        store.delete_source("sales")
    assert e.value.code == ErrorCode.IN_USE and "other" in str(e.value)
    store.delete_scenario("other")
    store.delete_source("sales")
    assert [s.id for s in store.list_scenarios()] == ["sales_report"]


def manifest() -> ThemeManifest:
    return ThemeManifest(
        source_path="corp.pptx", pptx_path="", sha256="a" * 64, slide_width=100, slide_height=50, layouts=[], roles=[]
    )


def test_themes_and_versions(store: SqliteMetadataStore):
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    v1 = ThemeVersionRecord(
        theme_id="corp", number=1, pptx_uri="/t/1.pptx", sha256="a" * 64, original_name="Шаблон.pptx",
        manifest=manifest(), imported_at=now,
    )  # fmt: skip
    rec = store.add_theme_version(v1, name="Корпоративный")
    assert (rec.id, rec.name, rec.version, rec.current.original_name) == ("corp", "Корпоративный", 1, "Шаблон.pptx")
    assert store.next_theme_version("corp") == 2
    v2 = v1.model_copy(update={"number": 2, "roles": {"title": "2147483649"}, "comment": "роли"})
    rec = store.add_theme_version(v2)
    assert (rec.name, rec.version, rec.current.roles) == ("Корпоративный", 2, {"title": "2147483649"})
    assert [v.number for v in store.theme_versions("corp")] == [1, 2]
    assert store.get_theme_version("corp", 1).roles == {}
    assert [v.number for v in store.find_theme_versions("a" * 64)] == [1, 2]
    store.save_scenario("r", scenario(), theme=("corp", 2))
    assert [s.id for s in store.scenarios_using_theme("corp")] == ["r"]
    store.delete_theme("corp")
    assert store.list_themes() == []


def test_runs(store: SqliteMetadataStore):
    store.save_scenario("r", scenario())
    t = datetime(2026, 10, 3, 12, tzinfo=UTC)
    assert store.next_run_seq("r") == 1
    m = HistoryManifest.for_source(SPEC)
    store.add_run(RunRecord(id="r-001", scenario_id="r", scenario_version=1, started_at=t, inputs_history={"s": m}))
    store.add_run(RunRecord(id="r-002", scenario_id="r", scenario_version=1, started_at=t.replace(hour=13)))
    assert store.next_run_seq("r") == 3
    assert [r.id for r in store.list_runs()] == ["r-002", "r-001"]
    assert [r.id for r in store.list_runs("r", limit=1)] == ["r-002"]
    res = RunResult(ok=True, scenario="Продажи", slides=3, issues=[Issue(message="мало данных")])
    rec = store.update_run(
        "r-001", status=RunStatus.OK, result=res, period=Period.parse("2026-03"), output_uri="/o/a.pptx", finished_at=t
    )
    assert (rec.status, rec.result.slides, rec.period.key, rec.inputs_history["s"].source_id) == (
        RunStatus.OK, 3, "2026-03", "sales",
    )  # fmt: skip
    assert store.interrupt_running() == ["r-002"]
    assert store.get_run("r-002").status == RunStatus.INTERRUPTED
    store.delete_scenario("r")
    assert store.list_runs() == []
