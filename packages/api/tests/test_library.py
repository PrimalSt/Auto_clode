"""Сопоставление столбцов при загрузке, сценарии, шаблоны, запуски и резервные копии в папке данных."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pptx import Presentation

from autogenerator.api import Home, MappingChoice
from autogenerator.contracts import AgenError, ErrorCode, RunRecord, RunStatus, SourceSpec

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "sales"
TEMPLATE = ROOT / "examples" / "templates" / "synthetic.pptx"
JAN, FEB, MAR = (EXAMPLE / "data" / "sales" / f"Продажи_2026-0{m}.csv" for m in (1, 2, 3))
PLAN = EXAMPLE / "data" / "plan" / "План_2026-Q1.xlsx"

SRC = SourceSpec.model_validate(
    {
        "id": "s",
        "name": "S",
        "period_column": "date",
        "columns": [
            {"id": "date", "name": "Дата", "dtype": "date"},
            {"id": "region", "name": "Регион"},
            {"id": "amount", "name": "Сумма", "dtype": "float"},
        ],
    }
)


def csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path: Path) -> Home:
    h = Home.open(tmp_path / "home", write=True)
    yield h
    h.close()


def example_home(home: Home) -> None:
    home.import_sources(EXAMPLE / "sources.yaml")
    for f in (JAN, FEB):
        home.upload("sales_crm", f)
    home.upload("sales_plan", PLAN)


# --- сопоставление --------------------------------------------------------------------


def test_accepted_mapping_is_remembered(home: Home, tmp_path: Path):
    spec, _ = home.draft_source(JAN, "sales", "Продажи")
    # в феврале у дат есть время: формат января к ним не подходит
    cols = [c.model_copy(update={"format": None}) for c in spec.columns]
    home.create_source(spec.model_copy(update={"columns": cols}))
    home.upload("sales", JAN)
    with pytest.raises(AgenError) as e:
        home.upload("sales", FEB)
    assert e.value.code == ErrorCode.SCHEMA_REVIEW
    review = e.value.details["files"][FEB.name]
    assert review["proposed"] == {"Дата": "order_date", "№ заказа": "order_no", "Сумма, руб.": "amount"}
    out = home.upload("sales", FEB, accept_mapping=True)
    assert out.record.period.key == "2026-02" and out.record.source_version == 2
    assert any("Запомнено сопоставление" in i.message for i in out.issues)
    cols = {c.id: c for c in home.source("sales").spec.columns}
    assert cols["order_date"].aliases == ["Дата"] and cols["amount"].aliases == ["Сумма, руб."]
    # В следующий раз то же переименование сопоставится само.
    again = tmp_path / "Продажи_2026-04.csv"
    again.write_text("Дата;№ заказа;Регион;Менеджер;Сумма, руб.\n01.04.2026;1;М;А;5\n", encoding="cp1251")
    rec = home.upload("sales", again).record
    assert rec.mapping["Дата"] == "order_date" and home.source("sales").version == 2


def test_explicit_mapping_declined_and_wrong_ids(home: Home, tmp_path: Path):
    home.create_source(SRC)
    home.upload("s", csv(tmp_path / "a.csv", "Дата;Регион;Сумма", ["01.01.2026;Москва;10", "02.01.2026;Казань;20"]))
    b = csv(tmp_path / "b.csv", "Дата;Область;Сумма заказа", ["01.02.2026;Москва;11", "02.02.2026;Казань;21"])
    with pytest.raises(AgenError) as e:
        home.upload("s", b, mapping={"Сумма заказа": "nope"})
    assert e.value.code == ErrorCode.SPEC_INVALID and "nope" in str(e.value)
    # Сумму подтверждаем, регион оставляем пустым; пара для несуществующего столбца — замечание.
    out = home.upload("s", b, mapping={"Сумма заказа": "amount", "Нет такого": "region"}, declined=["region"])
    assert out.record.mapping == {"Дата": "date", "Сумма заказа": "amount"}
    msgs = [i.message for i in out.issues]
    assert any("«Нет такого» → region не применено" in m for m in msgs)
    assert any("по вашему выбору" in m for m in msgs)
    assert home.source("s").spec.column("amount").aliases == ["Сумма заказа"]
    assert home.source("s").spec.column("region").aliases == []


def test_choose_mapping_callback(home: Home, tmp_path: Path):
    home.create_source(SRC)
    rows = ["01.02.2026;Москва;11;1", "02.02.2026;Казань;21;2"]
    b = csv(tmp_path / "b.csv", "Дата;Регион;Сумма, руб.;Сумма, евро", rows)
    asked = []

    def choose(spec: SourceSpec, files: dict) -> MappingChoice | None:
        rec = files["b.csv"]
        asked.append((rec.review, rec.proposed, {c.file_name for c in rec.candidates["amount"]}))
        return MappingChoice(pairs={"Сумма, руб.": "amount"})

    out = home.upload("s", b, choose_mapping=choose)
    # Два почти одинаковых кандидата: пары не предлагается, выбирает пользователь.
    assert asked == [(["amount"], {}, {"Сумма, евро", "Сумма, руб."})]
    assert out.record.mapping["Сумма, руб."] == "amount"
    # Отказ от выбора оставляет загрузку остановленной.
    c = csv(tmp_path / "c.csv", "Дата;Регион;Сумма, тенге;Сумма, евро", ["01.03.2026;Москва;11;1"])
    with pytest.raises(AgenError) as e:
        home.upload("s", c, choose_mapping=lambda spec, files: None)
    assert e.value.code == ErrorCode.SCHEMA_REVIEW


def test_saved_scenarios_decide_which_columns_matter(home: Home, tmp_path: Path):
    example_home(home)
    assert home.column_usage("sales_crm").required is None
    home.save_scenario(EXAMPLE / "scenario.yaml", "sales_report")
    usage = home.column_usage("sales_crm")
    assert usage.required is not None and {"order_no", "amount", "region"} <= set(usage.required)
    assert any("сценарий «Ежемесячный отчёт по продажам»" in d for d in usage.dependents["order_no"])
    # Столбец, нужный сценарию, пропал, и похожего нет: загрузка остановлена с объяснением.
    text = MAR.read_text(encoding="cp1251").replace("Менеджер", "Колонка X", 1)
    mar = tmp_path / "Продажи_2026-03.csv"
    mar.write_text(text, encoding="cp1251")
    with pytest.raises(AgenError) as e:
        home.upload("sales_crm", mar)
    assert e.value.code == ErrorCode.SCHEMA_BLOCKED and "top_managers" in str(e.value)
    out = home.upload("sales_crm", mar, mapping={"Колонка X": "manager"})
    assert out.record.mapping["Колонка X"] == "manager"
    assert "Колонка X" in home.source("sales_crm").spec.column("manager").aliases
    # Источник, на котором стоит сценарий, не удаляется.
    with pytest.raises(AgenError) as e:
        home.delete_source("sales_crm")
    assert e.value.code == ErrorCode.IN_USE and home.uploads("sales_crm")


# --- сценарии и шаблоны -----------------------------------------------------------------


def test_scenario_versions_theme_import_and_copy(home: Home, tmp_path: Path):
    example_home(home)
    saved = home.save_scenario(EXAMPLE / "scenario.yaml")
    rec = saved.record
    assert rec.version == 1 and not saved.errors, saved.issues
    assert saved.theme is not None and saved.theme.record.id == "synthetic"
    assert rec.current.theme_id == "synthetic" and rec.current.theme_version == 1
    assert rec.spec.theme == "synthetic" and "theme: synthetic" in home.scenario_text(rec.id)
    assert home.scenario_text(rec.id).startswith("# Ежемесячный отчёт по продажам")
    # Тот же файл — без новой версии; другой текст — новая версия.
    assert home.save_scenario(EXAMPLE / "scenario.yaml").record.version == 1
    changed = tmp_path / "scenario.yaml"
    changed.write_text(home.scenario_text(rec.id) + "\n# правка\n", encoding="utf-8")
    assert home.save_scenario(changed).record.version == 2
    assert [v.number for v in home.scenario_versions(rec.id)] == [1, 2]
    copy = home.copy_scenario(rec.id, "copy_report", "Копия отчёта")
    assert copy.name == "Копия отчёта" and 'name: "Копия отчёта"' in home.scenario_text("copy_report")
    with pytest.raises(AgenError) as e:
        home.delete_theme("synthetic")
    assert e.value.code == ErrorCode.IN_USE
    home.delete_scenario("copy_report")
    assert [s.id for s in home.scenarios()] == [rec.id]


def test_theme_roles_and_reimport_carry_over(home: Home, tmp_path: Path):
    example_home(home)
    sid = home.save_scenario(EXAMPLE / "scenario.yaml", "sales_report").record.id
    imp = home.set_theme_roles("synthetic", {"title_only": "2147483661"})
    assert imp.record.version == 2 and imp.record.current.roles == {"title_only": "2147483661"}
    assert imp.scenarios == [sid] and home.scenario(sid).current.theme_version == 2
    binding = imp.manifest.role("title_only")
    assert binding is not None and not binding.guessed and binding.layout_key == "2147483661"
    with pytest.raises(AgenError):
        home.set_theme_roles("synthetic", {"title_only": "1"})
    # Тот же файл — пропуск; пересохранённый (другой хеш) — новая версия с прежними ролями.
    assert home.import_theme(TEMPLATE).skipped
    resaved = tmp_path / "synthetic.pptx"
    Presentation(str(TEMPLATE)).save(str(resaved))
    imp = home.import_theme(resaved, "synthetic")
    assert not imp.skipped and imp.record.current.roles == {"title_only": "2147483661"}
    assert home.scenario(sid).current.theme_version == imp.record.version and not imp.lost
    assert home.theme_path("synthetic").is_file() and home.theme_path("synthetic", 1).is_file()
    out = home.export_theme("synthetic", tmp_path)
    assert out.name == f"synthetic_v{imp.record.version}.pptx" and out.is_file()


# --- запуски и резервные копии -----------------------------------------------------------


def test_run_journal_rerun_and_backups(home: Home, tmp_path: Path):
    example_home(home)
    sid = home.save_scenario(EXAMPLE / "scenario.yaml", "sales_report").record.id
    run = home.run_scenario(sid, output=tmp_path / "out")
    assert run.status == RunStatus.OK, run.result.issues if run.result else None
    assert run.id == "sales_report-001" and run.period is not None and run.period.key == "2026-02"
    assert run.scenario_version == 1 and (run.theme_id, run.theme_version) == ("synthetic", 1)
    assert run.source_versions == {"sales_crm": 1, "sales_plan": 1}
    assert len(run.inputs_history["sales"].uploads) == 2
    env = run.result.environment if run.result else None
    assert env is not None and "polars" in env.libraries and env.env_hash
    assert Path(run.output_uri).is_file() and Path(run.output_copy).parent == tmp_path / "out"
    assert home.run_output(run.id) == Path(run.output_copy)
    # Пересборка за прошлый период по текущей истории (F-609a).
    home.upload("sales_crm", MAR)
    later = home.run_scenario(sid)
    assert later.period is not None and later.period.key == "2026-03" and not later.period_given
    again = home.rerun(run.id)
    assert again.period is not None and again.period.key == "2026-02" and again.period_given
    assert [r.id for r in home.runs(sid)] == ["sales_report-003", "sales_report-002", "sales_report-001"]
    # Резервная копия и возврат к ней.
    b = home.backup("перед правкой")
    assert b.name.endswith("-manual.sqlite") and b.parent == home.folder.backups
    home.delete_run(run.id)
    assert len(home.runs(sid)) == 2 and not Path(run.output_uri).exists()
    safety = home.restore_backup(b)
    assert safety.is_file() and len(home.runs(sid)) == 3
    assert home.backups()[0].path == safety


def test_failed_run_is_recorded(home: Home, tmp_path: Path):
    example_home(home)
    sid = home.save_scenario(EXAMPLE / "scenario.yaml", "sales_report").record.id
    run = home.run_scenario(sid, period="2025-01")
    assert run.status == RunStatus.FAILED and run.output_uri is None
    assert run.result is not None and run.result.errors


def test_interrupted_runs_are_marked_on_open(tmp_path: Path):
    with Home.open(tmp_path / "home", write=True) as h:
        example_home(h)
        sid = h.save_scenario(EXAMPLE / "scenario.yaml", "sales_report").record.id
        started = datetime(2026, 10, 3, 10, tzinfo=UTC)
        h.store.add_run(RunRecord(id=f"{sid}-001", scenario_id=sid, scenario_version=1, started_at=started))
    with Home.open(tmp_path / "home", write=True) as h:
        assert h.run_record(f"{sid}-001").status == RunStatus.INTERRUPTED
