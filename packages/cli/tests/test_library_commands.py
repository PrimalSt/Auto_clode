"""agen scenario, theme (папка данных), run <id>, runs, backup и сопоставление при загрузке."""

from pathlib import Path

from typer.testing import CliRunner

from autogenerator.cli.main import app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "sales"
DATA = EXAMPLE / "data"
runner = CliRunner()


def agen(*args: str | Path, code: int = 0) -> str:
    r = runner.invoke(app, [str(a) for a in args])
    assert r.exit_code == code, r.output
    return r.output


def example_home() -> None:
    agen("source", "import", EXAMPLE / "sources.yaml")
    agen("upload", "add", "sales_crm", DATA / "sales" / "Продажи_2026-01.csv", DATA / "sales" / "Продажи_2026-02.csv")
    agen("upload", "add", "sales_plan", DATA / "plan" / "План_2026-Q1.xlsx")


def test_scenario_run_runs_and_backup(tmp_path: Path):
    example_home()
    out = agen("scenario", "add", EXAMPLE / "scenario.yaml", "--id", "sales")
    assert "Шаблон «synthetic» (synthetic): версия 1" in out and "Сценарий «sales»" in out and "agen run sales" in out
    assert "sales  " in agen("scenario", "list")
    out = agen("scenario", "show", "sales")
    assert "Шаблон: synthetic v1" in out and "Вход sales: источник sales_crm (основной)" in out
    assert "theme: synthetic" in agen("scenario", "export", "sales")
    assert "Сценарий в порядке." in agen("validate", "sales")

    out = agen("run", "sales", "-o", tmp_path / "out")
    assert "Запуск sales-001" in out and "готово" in out and "период 2026-02" in out
    assert (tmp_path / "out" / "Отчёт_продажи_2026-02.pptx").exists()
    agen("upload", "add", "sales_crm", DATA / "sales" / "Продажи_2026-03.csv")
    assert "период 2026-03" in agen("run", "sales")
    out = agen("runs", "rerun", "sales-001")
    assert "Запуск sales-003" in out and "период 2026-02 (задан)" in out
    out = agen("runs", "list")
    assert out.index("sales-003") < out.index("sales-001")
    out = agen("runs", "show", "sales-001", "-v")
    assert "Источники: sales_crm v1, sales_plan v1" in out and "polars" in out and "input:sales" in out
    out = agen("run", "sales", "--theme", "x.pptx", code=2)
    assert "у сохранённого сценария" in out

    out = agen("theme", "list")
    assert "synthetic" in out and "сценарии: sales" in out
    out = agen("theme", "roles", "synthetic", "title_only=2147483661")
    assert "версия 2" in out and "title_only             → «Только заголовок» (id 2147483661), подтверждена" in out
    assert "роли макетов не изменились" in agen("theme", "roles", "synthetic", "title_only=2147483661")
    out = agen("theme", "import", ROOT / "examples" / "templates" / "synthetic.pptx")
    assert "этот файл уже загружен (версия 2)" in out
    assert "шаблон synthetic v2" in agen("scenario", "list")
    assert "Роли макетов" in agen("theme", "check", "synthetic", code=1)  # в синтетическом шаблоне есть ошибки
    out = agen("theme", "export", "synthetic", "-o", tmp_path)
    assert (tmp_path / "synthetic_v2.pptx").exists()
    out = agen("theme", "versions", "synthetic")
    assert "1  " in out and "подтверждены роли макетов: title_only → 2147483661" in out
    assert "используют сценарии: sales" in agen("theme", "delete", "synthetic", "--yes", code=1)

    out = agen("backup", "create")
    name = Path(out.split("Резервная копия: ")[1].strip()).name
    assert name in agen("backup", "list")
    agen("runs", "delete", "sales-002")
    out = agen("backup", "restore", name, "--yes")
    assert "Прежняя база сохранена" in out and "sales-002" in agen("runs", "list")

    agen("scenario", "copy", "sales", "sales2", "--name", "Копия")
    agen("scenario", "delete", "sales2", "--yes")
    assert "sales2" not in agen("scenario", "list")


def test_upload_mapping_options(tmp_path: Path):
    agen("source", "create", "sales", "--from", DATA / "sales" / "Продажи_2026-03.csv", "--name", "Продажи")
    agen("upload", "add", "sales", DATA / "sales" / "Продажи_2026-03.csv")
    text = (DATA / "sales" / "Продажи_2026-03.csv").read_text(encoding="cp1251")
    renamed = tmp_path / "Продажи_2026-01.csv"
    jan = text.replace("Менеджер", "Ответственный менеджер", 1).replace("Регион", "Округ", 1)
    renamed.write_text(jan.replace(".03.2026", ".01.2026"), encoding="cp1251")
    out = agen("upload", "add", "sales", renamed, code=1)
    assert "«Менеджер» (id manager) → «Ответственный менеджер»" in out and "--map" in out
    out = agen("upload", "add", "sales", renamed, "--accept-mapping", "--empty", "region")
    assert "период 2026-01 — в истории" in out and "Запомнено сопоставление: «Ответственный менеджер» → manager" in out
    assert "по вашему выбору" in out
    out = agen("source", "show", "sales")
    assert "ещё: Ответственный менеджер" in out
    # Своё сопоставление через --map; ошибочный id — понятная ошибка.
    may = tmp_path / "Продажи_2026-05.csv"
    may.write_text(text.replace("Регион", "Округ", 1).replace(".03.2026", ".05.2026"), encoding="cp1251")
    assert "нет столбцов: nope" in agen("upload", "add", "sales", may, "--map", "Округ=nope", code=1)
    out = agen("upload", "add", "sales", may, "--map", "Округ=region")
    assert "Запомнено сопоставление: «Округ» → region" in out


def test_console_mapping_prompt(monkeypatch):
    import sys

    import typer

    from autogenerator.cli import data
    from autogenerator.contracts import MappingCandidate, ReconcileResult, SourceSpec

    spec = SourceSpec.model_validate(
        {
            "id": "s",
            "name": "S",
            "period_column": "date",
            "columns": [
                {"id": "date", "name": "Дата", "dtype": "date"},
                {"id": "amount", "name": "Сумма", "dtype": "float"},
                {"id": "note", "name": "Комментарий"},
            ],
        }
    )
    cands = [
        MappingCandidate(file_name="Сумма, руб.", score=0.7, name_score=0.7),
        MappingCandidate(file_name="Сумма, евро", score=0.68, name_score=0.68),
    ]
    files = {
        "f.csv": ReconcileResult(
            status="needs_review",
            review=["amount", "note"],
            candidates={"amount": cands, "note": [MappingCandidate(file_name="Примечание", score=0.5, name_score=0.5)]},
            dependents={"amount": ["сценарий «Отчёт», вход s: наборы by_region"]},
        )
    }

    class View:
        def pause(self) -> None:
            pass

    answers: list[tuple[int | None, int]] = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def prompt(text, type=None, default=None):
        answers.append((default, picks.pop(0)))
        return answers[-1][1]

    monkeypatch.setattr(typer, "prompt", prompt)
    # Нужный столбец без явного кандидата: подсказки по умолчанию нет, 0 — отмена загрузки.
    picks = [0]
    assert data._choose_mapping(spec, files, View()) is None
    assert answers == [(None, 0)]
    # Выбран второй кандидат; ненужный столбец по Enter (0) остаётся пустым.
    picks, answers = [2, 0], []
    choice = data._choose_mapping(spec, files, View())
    assert choice is not None and choice.pairs == {"Сумма, евро": "amount"} and choice.declined == ["note"]
    assert answers == [(None, 2), (0, 0)]
