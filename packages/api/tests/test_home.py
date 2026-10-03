from datetime import date
from pathlib import Path

import polars as pl
import pytest

from autogenerator.api import Home, find_inputs, load_scenario, run
from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    OverlapPolicy,
    Period,
    PeriodFrom,
    PeriodUnit,
    SourceSpec,
    UploadStatus,
)

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "sales"
JAN, FEB, MAR = (EXAMPLE / "data" / "sales" / f"Продажи_2026-0{m}.csv" for m in (1, 2, 3))


def csv(path: Path, rows: list[str]) -> Path:
    path.write_text("Дата;Регион;Сумма\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path: Path) -> Home:
    h = Home.open(tmp_path / "home", write=True)
    yield h
    h.close()


def test_draft_create_upload_history(home: Home, tmp_path: Path):
    spec, snap = home.draft_source(JAN, "sales", "Продажи")
    assert spec.period_type == PeriodUnit.MONTH and snap.columns[0].profile is not None
    home.create_source(spec)
    out = home.upload("sales", JAN)
    rec = out.record
    assert (rec.seq, rec.period.key, rec.status, rec.rows) == (1, "2026-01", UploadStatus.ACTIVE, 751)
    assert rec.profile[spec.period_column].exact and rec.schema_snapshot is not None
    # В февральской выгрузке столбцы названы иначе: без aliases она не подходит.
    with pytest.raises(AgenError) as e:
        home.upload("sales", FEB)
    assert e.value.code == ErrorCode.SCHEMA_BLOCKED and "столбца периода" in str(e.value)
    home.upload("sales", MAR)
    # Тот же файл второй раз — только с force.
    with pytest.raises(AgenError) as e:
        home.upload("sales", JAN)
    assert e.value.code == ErrorCode.ALREADY_EXISTS
    rep = home.coverage("sales")
    assert [(c.period.key, c.state) for c in rep.cells] == [
        ("2026-01", "covered"),
        ("2026-02", "gap"),
        ("2026-03", "covered"),
    ]
    assert [g.start.isoformat() for g in rep.gaps if g.start] == ["2026-02-01"]
    assert home.default_period("sales").key == "2026-03"
    n = home.export_history("sales", tmp_path / "h.parquet")
    assert n == pl.read_parquet(tmp_path / "h.parquet").height > 1000


def test_ask_policy_and_cast_review(home: Home, tmp_path: Path):
    spec = SourceSpec.model_validate(
        {
            "id": "s",
            "name": "S",
            "period_column": "date",
            "overlap_policy": "ask",
            "columns": [
                {"id": "date", "name": "Дата", "dtype": "date"},
                {"id": "region", "name": "Регион"},
                {"id": "amount", "name": "Сумма", "dtype": "float"},
            ],
        }
    )
    home.create_source(spec)
    home.upload("s", csv(tmp_path / "a.csv", ["01.01.2026;Москва;1", "02.01.2026;Казань;2"]))
    fix = csv(tmp_path / "b.csv", ["01.01.2026;Москва;10"])
    # Пересечение без выбора: загрузка не записывается и файлов не остаётся.
    with pytest.raises(AgenError) as e:
        home.upload("s", fix)
    assert e.value.code == ErrorCode.OVERLAP_CHOICE
    assert len(home.uploads("s")) == 1
    asked = []
    out = home.upload("s", fix, choose_policy=lambda r: asked.append(r.overlaps) or OverlapPolicy.APPEND)
    assert asked and out.record.overlap_policy == OverlapPolicy.APPEND
    assert any("двойного учёта" in i.message for i in out.issues)
    # Ошибка в столбце периода — загрузка на проверке и не входит в историю, пока её не примут.
    bad = home.upload("s", csv(tmp_path / "c.csv", ["01.02.2026;Москва;1", "32.02.2026;Тула;2"]))
    assert bad.record.status == UploadStatus.NEEDS_REVIEW
    assert [u.period.key for u in home.history("s").active_uploads] == ["2026-01", "2026-01"]
    home.set_upload_status(bad.record.id, UploadStatus.ACTIVE)
    assert len(home.history("s").active_uploads) == 3


def test_period_override_and_rows_outside(home: Home, tmp_path: Path):
    spec, _ = home.draft_source(JAN, "sales", period_type=PeriodUnit.RANGE)
    home.create_source(spec)
    rec = home.upload("sales", JAN).record
    assert rec.period.unit == PeriodUnit.RANGE and rec.rows_outside_period == 0
    rec = home.set_upload_period(rec.id, Period.parse("2026-01-01..2026-01-15"))
    assert rec.rows_outside_period > 0 and rec.period_from_data != rec.period


def test_source_change_keeps_ids(home: Home):
    spec, _ = home.draft_source(JAN, "sales")
    home.create_source(spec)
    home.upload("sales", JAN)
    renamed = spec.model_dump()
    renamed["columns"][1]["id"] = "number"
    with pytest.raises(AgenError) as e:
        home.update_source(SourceSpec.model_validate(renamed))
    assert e.value.code == ErrorCode.SOURCE_CHANGE
    assert home.update_source(SourceSpec.model_validate(renamed), force=True).version == 2


def test_delete_upload_removes_files(home: Home):
    spec, _ = home.draft_source(JAN, "sales")
    home.create_source(spec)
    rec = home.upload("sales", JAN).record
    assert Path(rec.data_uri).exists()
    home.delete_upload(rec.id)
    assert not Path(rec.data_uri).exists() and home.uploads("sales") == []


def test_second_writer_is_refused(tmp_path: Path):
    with Home.open(tmp_path / "home", write=True):
        with pytest.raises(AgenError) as e:
            Home.open(tmp_path / "home", write=True)
        assert e.value.code == ErrorCode.DATA_FOLDER_LOCKED
        # Читать можно и пока другой процесс пишет.
        with Home.open(tmp_path / "home") as reader:
            assert reader.sources() == [] and not reader.writable


def test_run_takes_history_from_home(tmp_path: Path):
    with Home.open(tmp_path / "home", write=True) as h:
        h.import_sources(EXAMPLE / "sources.yaml")
        for f in (JAN, FEB):
            h.upload("sales_crm", f)
        h.upload("sales_plan", EXAMPLE / "data" / "plan" / "План_2026-Q1.xlsx")
    res = run(EXAMPLE / "scenario.yaml", home=tmp_path / "home", output=tmp_path / "r.pptx")
    assert res.ok, res.issues
    # В папке данных — январь и февраль, а в папке data примера есть и март.
    assert sorted(res.from_home) == ["plan", "sales"] and res.period is not None and res.period.key == "2026-02"
    files = run(EXAMPLE / "scenario.yaml", home=tmp_path / "home", use_home=False, output=tmp_path / "f.pptx")
    assert files.from_home == [] and files.period is not None and files.period.key == "2026-03"


def test_find_inputs_takes_every_export_format(tmp_path: Path):
    # Выгрузки одного входа лежат в папке вперемешку: .xlsx и .xlsb, .xls, CSV; прочее — не данные.
    d = tmp_path / "sales"
    d.mkdir()
    for name in ("янв.xlsx", "фев.XLSB", "мар.xls", "апр.csv", "май.xlsm", "заметки.docx", "план.pdf"):
        (d / name).touch()
    files = find_inputs(load_scenario(EXAMPLE / "scenario.yaml"), tmp_path)
    assert [Path(f).name for f in files["sales"]] == ["апр.csv", "май.xlsm", "мар.xls", "фев.XLSB", "янв.xlsx"]
    assert "plan" not in files


def test_parts_of_one_export_and_duplicate_set(home: Home, tmp_path: Path):
    a = csv(tmp_path / "часть1.csv", ["01.03.2026;Москва;1", "02.03.2026;Казань;2"])
    b = csv(tmp_path / "часть2.csv", ["03.03.2026;Москва;3"])
    spec, _ = home.draft_source(a, "s")
    home.create_source(spec)
    out = home.upload("s", [a, b])
    r = out.record
    assert (r.seq, r.rows, r.period.key, r.original_name) == (1, 3, "2026-03", "часть1.csv + часть2.csv")
    with pytest.raises(AgenError) as e:
        home.upload("s", [a, b])
    assert e.value.code == ErrorCode.ALREADY_EXISTS and "Эти файлы уже загружены" in str(e.value)
    # Тот же файл отдельно — другая загрузка, его можно загрузить.
    assert home.upload("s", a, overlap_policy=OverlapPolicy.REPLACE_PERIOD).record.seq == 2


def test_snapshot_period_can_be_fixed_after_upload(home: Home, tmp_path: Path):
    f = tmp_path / "Клиенты_янв_2026.csv"
    f.write_text("Клиент;Дата регистрации\na;2019-03-14\nb;2025-08-02\n", encoding="utf-8")
    spec, _ = home.draft_source(f, "clients")
    assert spec.period_from == PeriodFrom.UPLOAD
    home.create_source(spec)
    up = home.upload("clients", f).record
    assert up.period.key == "2026-01"
    home.set_upload_period(up.id, Period.parse("2026-02"))
    out = tmp_path / "h.parquet"
    assert home.export_history("clients", out) == 2
    assert set(pl.read_parquet(out)["period"].to_list()) == {date(2026, 2, 1)}
