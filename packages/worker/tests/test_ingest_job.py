"""Задание ingest: сверка, запись, период, пересечения, профиль — на файлах примера."""

from pathlib import Path

import pytest

from autogenerator.contracts import (
    AgenError,
    DType,
    ErrorCode,
    IngestRequest,
    IssueLevel,
    Period,
    PeriodFrom,
    PeriodUnit,
    SourceSpec,
)
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


# --- Выгрузки по образцу настоящих (синтетические данные) ------------------------------


def accounts_csv(path: Path, month: str) -> Path:
    """Срез учётных записей на конец месяца: столбца с отчётным месяцем нет, месяц — в
    названиях столбцов и в имени файла; даты в файле — за много лет."""
    path.write_text(
        f"Логин;Активность;Дата создания УЗ;Последняя авторизация;Были ли авторизации в {month} да/нет;"
        f"в {month} на платформе web\n"
        "a;T;2016-12-30 10:11:32;2025-02-03 12:55:57;да;2\n"
        "b;F;2021-07-28 09:50:37;;нет;\n"
        "c;T;2025-09-26 19:10:12;2026-02-16 12:54:00;да;1\n",
        encoding="utf-8",
    )
    return path


def test_snapshot_export_gets_period_from_file_name(tmp_path: Path):
    jan = accounts_csv(tmp_path / "accounts_jan_2026.csv", "январе")
    spec, _ = draft_source(jan, "accounts")
    assert spec.period_from == PeriodFrom.UPLOAD and spec.period_type == PeriodUnit.MONTH
    assert spec.column("aktivnost").dtype == DType.BOOL
    apr = accounts_csv(tmp_path / "accounts_apr_2026.csv", "апреле")
    res = ingest_upload(
        IngestRequest(source=spec, path=str(apr), upload_id="u1", upload_seq=1, out_dir=str(tmp_path / "u1"))
    )
    assert res.period.key == "2026-04" and res.upload.rows == 3
    assert res.reconcile.mapping["в апреле на платформе web"] == "v_month_na_platforme_web"
    assert not [i for i in res.issues if i.level == IssueLevel.WARNING]
    nameless = accounts_csv(tmp_path / "accounts.csv", "мае")
    req = IngestRequest(source=spec, path=str(nameless), upload_id="u2", upload_seq=2, out_dir=str(tmp_path / "u2"))
    with pytest.raises(AgenError) as e:
        ingest_upload(req)
    assert e.value.code == ErrorCode.PERIOD_INVALID and "--period" in (e.value.hint or "")
    res = ingest_upload(req.model_copy(update={"period": Period.parse("2026-05")}))
    assert res.period.key == "2026-05"


def test_missing_column_is_a_warning(tmp_path: Path):
    jan = accounts_csv(tmp_path / "accounts_jan_2026.csv", "январе")
    spec, _ = draft_source(jan, "accounts")
    feb = tmp_path / "accounts_feb_2026.csv"
    feb.write_text("Логин;Активность\na;T\n", encoding="utf-8")
    res = ingest_upload(
        IngestRequest(source=spec, path=str(feb), upload_id="u1", upload_seq=1, out_dir=str(tmp_path / "u1"))
    )
    warnings = [i.message for i in res.issues if i.level == IssueLevel.WARNING]
    assert any("Последняя авторизация" in w and "пустой" in w for w in warnings)


def test_period_column_is_the_one_within_a_month(tmp_path: Path):
    """У заказов документов три столбца дат: дата заказа — внутри месяца выгрузки, а
    расчётные периоды документов — за годы. Периодом выбирается первая."""
    f = tmp_path / "docs.csv"
    f.write_text(
        "DFNUMBER;DFCREATED;DFNUMBER.1;DFDATE_BEGIN;DFDATE_END\n"
        "26-342059;01.04.2026 00:27:11;461018823707;2024-12-01 00:00:00.000;2024-12-31 00:00:00.000\n"
        "26-342064;15.04.2026 10:00:00;596001469690;2016-01-01 00:00:00.000;2016-01-31 00:00:00.000\n"
        "26-342066;30.04.2026 23:51:12;765000007985;2026-04-01 00:00:00.000;2026-04-30 23:59:59.000\n",
        encoding="utf-8",
    )
    spec, _ = draft_source(f, "docs")
    assert spec.period_from == PeriodFrom.COLUMN and spec.period_column == "dfcreated"
    assert spec.period_type == PeriodUnit.MONTH
    assert spec.column("dfnumber_1").dtype == DType.STRING  # номера счетов — текст


def test_parts_are_concatenated(tmp_path: Path):
    a = tmp_path / "part1.csv"
    a.write_text("Дата;Сумма\n01.03.2026;1\n02.03.2026;2\n", encoding="utf-8")
    b = tmp_path / "part2.csv"
    b.write_text("Сумма;Дата\n3;03.03.2026\n", encoding="utf-8")
    spec, _ = draft_source(a, "s")
    res = ingest_upload(
        IngestRequest(
            source=spec, path=str(a), parts=[str(b)], upload_id="u1", upload_seq=1, out_dir=str(tmp_path / "u1")
        )
    )
    assert res.upload.rows == 3 and res.period.key == "2026-03"
