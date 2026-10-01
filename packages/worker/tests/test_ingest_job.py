"""Задание ingest: сверка, запись, период, пересечения, профиль — на файлах примера."""

from pathlib import Path

from autogenerator.contracts import DType, IngestRequest, PeriodUnit, SourceSpec
from autogenerator.contracts.yaml_io import load_model_list
from autogenerator.worker import draft_source, ingest_upload

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "sales"
SOURCES = {s.id: s for s in load_model_list(SourceSpec, EXAMPLE / "sources.yaml")}


def request(tmp_path: Path, source: str, file: Path, **kw) -> IngestRequest:
    return IngestRequest(
        source=SOURCES[source], path=str(file), upload_id="u1", upload_seq=1, out_dir=str(tmp_path / "u1"), **kw
    )


def test_csv_upload_with_renamed_columns(tmp_path: Path):
    res = ingest_upload(request(tmp_path, "sales_crm", EXAMPLE / "data" / "sales" / "Продажи_2026-02.csv"))
    assert res.reconcile.mapping["Сумма, руб."] == "amount"
    assert res.period.key == "2026-02" and res.period == res.period_from_data
    assert res.snapshot.sample_rows > 0 and res.profile["amount"].exact
    by_name = {c.source_name: c for c in res.snapshot.columns}
    assert by_name["Сумма, руб."].profile is not None


def test_excel_upload_reads_sheet_once(tmp_path: Path):
    res = ingest_upload(request(tmp_path, "sales_plan", EXAMPLE / "data" / "plan" / "План_2026-Q1.xlsx"))
    # Снимок по шапке (лист не читается второй раз), типы — из источника.
    assert res.snapshot.sample_rows == 0
    assert {c.source_name: c.dtype for c in res.snapshot.columns}["Месяц"] == DType.DATE
    assert res.period.key == "2026-Q1" and res.upload.rows == 15


def test_draft_source_from_excel(tmp_path: Path):
    spec, snap = draft_source(EXAMPLE / "data" / "plan" / "План_2026-Q1.xlsx", "plan")
    assert snap.options.header_row == 2 and spec.period_column == "month"
    assert spec.period_type == PeriodUnit.QUARTER
