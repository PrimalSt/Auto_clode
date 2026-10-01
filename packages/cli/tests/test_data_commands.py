from pathlib import Path

from typer.testing import CliRunner

from autogenerator.cli.main import app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples" / "sales"
DATA = EXAMPLE / "data"
runner = CliRunner()


def agen(*args: str | Path) -> str:
    r = runner.invoke(app, [str(a) for a in args])
    assert r.exit_code == 0, r.output
    return r.output


def test_source_from_file_upload_and_history(tmp_path: Path):
    out = agen("source", "create", "sales", "--from", DATA / "sales" / "Продажи_2026-01.csv", "--name", "Продажи")
    assert "order_date  date" in out and "Источник «sales» создан" in out
    out = agen("upload", "add", "sales", DATA / "sales" / "Продажи_2026-01.csv", DATA / "sales" / "Продажи_2026-03.csv")
    assert "#1 Продажи_2026-01.csv: 751 строк, период 2026-01 — в истории" in out
    assert "#2 Продажи_2026-03.csv" in out
    out = agen("history", "sales")
    assert "2026 ■   ·   ■" in out
    assert "Пропуски: 2026-02-01 … 2026-03-01" in out
    assert "Отчётный период по умолчанию: 2026-03" in out
    out = agen("source", "list")
    assert "sales" in out and "загрузок 2, 2026-01 … 2026-03" in out
    # Повторная загрузка того же файла и файл с другими названиями столбцов — ошибки.
    r = runner.invoke(app, ["upload", "add", "sales", str(DATA / "sales" / "Продажи_2026-02.csv")])
    assert r.exit_code == 1 and "столбца периода" in r.output


def test_import_export_and_set(tmp_path: Path):
    out = agen("source", "import", EXAMPLE / "sources.yaml")
    assert "sales_crm: создан" in out and "sales_plan: создан" in out
    out = agen("source", "set", "sales_crm", "--overlap", "append", "--comment", "части выгрузки")
    assert "версия настроек 2" in out and "добавить строки" in out
    y = tmp_path / "s.yaml"
    agen("source", "export", "sales_crm", "-o", y)
    assert "overlap_policy: append" in y.read_text(encoding="utf-8")
    out = agen("source", "show", "sales_crm", "--versions")
    assert "версия 2" in out and "части выгрузки" in out
    out = agen("source", "import", y)
    assert "sales_crm: без изменений" in out


def test_review_accept_and_run_from_home(tmp_path: Path):
    agen("source", "import", EXAMPLE / "sources.yaml")
    bad = tmp_path / "Продажи_2026-01.csv"
    text = (DATA / "sales" / "Продажи_2026-01.csv").read_text(encoding="cp1251")
    lines = text.splitlines()
    lines[5] = "32.01.2026" + lines[5][lines[5].index(";") :]
    bad.write_text("\n".join(lines) + "\n", encoding="cp1251")
    out = agen("upload", "add", "sales_crm", bad)
    assert "на проверке" in out and "agen upload accept" in out
    upload_id = out.split("agen upload accept ")[1].split(";")[0]
    agen("upload", "accept", upload_id)
    agen("upload", "add", "sales_crm", DATA / "sales" / "Продажи_2026-03.csv")
    agen("upload", "add", "sales_plan", DATA / "plan" / "План_2026-Q1.xlsx")
    out = agen("upload", "show", upload_id)
    assert "date (date): 1 не распознано, например 32.01.2026" in out and "Профиль по всей загрузке" in out
    out = agen("run", EXAMPLE / "scenario.yaml", "-o", tmp_path / "r.pptx")
    assert "Вход sales (история из папки данных)" in out and "Отчётный период: 2026-03" in out
    assert (tmp_path / "r.pptx").exists()
    agen("upload", "exclude", upload_id)
    assert "исключена" in agen("upload", "list", "sales_crm")


def test_inspect_shows_profile_and_preview():
    out = agen("inspect", DATA / "plan" / "План_2026-Q1.xlsx", "--preview", "2")
    assert "заголовки в строке 2" in out and "Профиль по выборке" in out and "Первые строки" in out
