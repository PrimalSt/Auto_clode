from pathlib import Path

from pptx import Presentation
from typer.testing import CliRunner

from autogenerator.cli.main import app

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "examples" / "sales" / "scenario.yaml"
runner = CliRunner()


def test_run_example(tmp_path: Path):
    out = tmp_path / "отчёт.pptx"
    r = runner.invoke(app, ["run", str(SCENARIO), "-o", str(out)])
    assert r.exit_code == 0, r.output
    assert "Отчётный период: 2026-03" in r.output
    assert "Готово:" in r.output
    assert len(Presentation(str(out)).slides) == 6


def test_run_with_explicit_inputs_and_period(tmp_path: Path):
    data = ROOT / "examples" / "sales" / "data"
    r = runner.invoke(
        app,
        [
            "run",
            str(SCENARIO),
            "--output-dir",
            str(tmp_path),
            "--period",
            "2026-01",
            "-i",
            f"sales={data / 'sales' / 'Продажи_2026-01.csv'}",
            "-i",
            f"plan={data / 'plan' / 'План_2026-Q1.xlsx'}",
        ],
    )
    # Январь — первый месяц: прошлого месяца в истории нет, это предупреждение, а не ошибка.
    assert r.exit_code == 0, r.output
    assert (tmp_path / "Отчёт_продажи_2026-01.pptx").exists()
    assert "нет загрузок входа «sales» за" in r.output


def test_validate(tmp_path: Path):
    r = runner.invoke(app, ["validate", str(SCENARIO)])
    assert r.exit_code == 0 and "Сценарий в порядке" in r.output
    bad = tmp_path / "scenario.yaml"
    bad.write_text(SCENARIO.read_text(encoding="utf-8").replace("fn: sum", "fn: summ", 1), encoding="utf-8")
    (tmp_path / "sources.yaml").write_text(
        (SCENARIO.parent / "sources.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    r = runner.invoke(
        app,
        ["validate", str(bad), "--theme", str(ROOT / "examples" / "templates" / "synthetic.pptx")],
    )
    assert r.exit_code == 1
    assert "summ" in r.output


def test_inspect():
    f = ROOT / "examples" / "sales" / "data" / "sales" / "Продажи_2026-02.csv"
    r = runner.invoke(app, ["inspect", str(f)])
    assert r.exit_code == 0, r.output
    assert "кодировка cp1251, разделитель ';'" in r.output
    assert "Сумма, руб." in r.output


def test_modules():
    r = runner.invoke(app, ["modules"])
    assert r.exit_code == 0, r.output
    assert "ОШИБКА" not in r.output
    for name in ("csv", "xlsx", "dedupe", "quarter_to_date", "sum", "chart"):
        assert name in r.output


def test_bad_input_argument():
    r = runner.invoke(app, ["run", str(SCENARIO), "-i", "sales.csv"])
    assert r.exit_code == 2
