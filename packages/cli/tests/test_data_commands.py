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
    assert "Пропуски: 2026-02\n" in out
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


def test_snapshot_source_and_parts(tmp_path: Path):
    jan = tmp_path / "Клиенты_январь_2026.csv"
    jan.write_text("Клиент;Дата регистрации;Были ли покупки в январе да/нет\na;2019-03-14;да\n", encoding="utf-8")
    out = agen("source", "create", "clients", "--from", jan, "--upload")
    assert "Период: задаётся при загрузке" in out and "считается срезом на месяц" in out
    assert "#1 Клиенты_январь_2026.csv: 1 строк, период 2026-01" in out and "по имени файла" in out
    feb = tmp_path / "Клиенты_февраль_2026.csv"
    feb.write_text("Клиент;Дата регистрации;Были ли покупки в феврале да/нет\nb;2020-06-01;нет\n", encoding="utf-8")
    out = agen("upload", "add", "clients", feb, "-v")
    assert "период 2026-02" in out and "с другим месяцем в названии" in out

    p1 = tmp_path / "Документы_ч1.csv"
    p1.write_text("Дата;Сумма\n01.03.2026;1\n02.03.2026;2\n", encoding="utf-8")
    p2 = tmp_path / "Документы_ч2.csv"
    p2.write_text("Дата;Сумма\n03.03.2026;3\n", encoding="utf-8")
    agen("source", "create", "orders", "--from", p1)
    out = agen("upload", "add", "orders", p1, p2, "--concat")
    assert "#1 Документы_ч1.csv + Документы_ч2.csv: 3 строк, период 2026-03" in out


def test_second_part_uploaded_separately_replaces_first(tmp_path: Path):
    p1 = tmp_path / "Заказы_ч1.csv"
    p1.write_text("Дата;Сумма\n01.03.2026;1\n02.03.2026;2\n", encoding="utf-8")
    p2 = tmp_path / "Заказы_ч2.csv"
    p2.write_text("Дата;Сумма\n03.03.2026;3\n", encoding="utf-8")
    agen("source", "create", "orders", "--from", p1, "--upload")
    out = agen("upload", "add", "orders", p2)
    assert "Заменяет загрузку #1 Заказы_ч1.csv за тот же период 2026-03" in out
    assert "Если это части одной выгрузки, загрузите их одной командой с --concat" in out
    # Файл с другими столбцами на часть той же выгрузки не похож: подсказки нет.
    p3 = tmp_path / "Заказы_март.csv"
    p3.write_text("Дата;Сумма;Комментарий\n04.03.2026;4;новый\n", encoding="utf-8")
    out = agen("upload", "add", "orders", p3)
    assert "Заменяет загрузки #1 Заказы_ч1.csv, #2 Заказы_ч2.csv за тот же период 2026-03" in out
    assert "--concat" not in out
    # Загрузка на проверке ничего не заменяет, пока её не примут.
    p4 = tmp_path / "Заказы_исправленные.csv"
    p4.write_text("Дата;Сумма\n05.03.2026;5\n06.03.2026;6\n32.03.2026;7\n", encoding="utf-8")
    out = agen("upload", "add", "orders", p4)
    assert "на проверке" in out and "После принятия заменит загрузки #1 Заказы_ч1.csv, #2" in out
    assert "Заменяет" not in out and "в историю не входят" not in out


def test_ragged_rows(tmp_path: Path):
    clean = tmp_path / "Заказы_2026-02.csv"
    clean.write_text("Дата;Регион;Сумма\n01.02.2026;Москва;1;\n02.02.2026;Тула;2;\n", encoding="utf-8")
    out = agen("source", "create", "orders", "--from", clean, "--upload")
    assert "#1 Заказы_2026-02.csv: 2 строк" in out and "ragged" not in out and "полей больше" not in out
    f = tmp_path / "Заказы_2026-03.csv"
    f.write_text(
        "Дата;Регион;Сумма\n01.03.2026;Москва;1;\n02.03.2026;Тула;2;лишнее\n03.03.2026;Псков;3;\n", encoding="utf-8"
    )
    r = runner.invoke(app, ["upload", "add", "orders", str(f)])
    assert r.exit_code == 1 and "В строке 3 файла Заказы_2026-03.csv 4 полей" in r.output
    assert "--ragged truncate" in r.output
    out = agen("upload", "add", "orders", f, "--ragged", "truncate")
    note = "В файле Заказы_2026-03.csv строк, где полей больше, чем в шапке: 1 (первая — строка 3)"
    assert "#2 Заказы_2026-03.csv: 3 строк" in out and f"! {note}" in out
    upload_id = agen("upload", "list", "orders").splitlines()[-1].split("[")[1].rstrip("]")
    assert f"· {note}" in agen("upload", "show", upload_id)
    # Параметр сохраняется в источнике, если задан при создании.
    out = agen("source", "create", "orders2", "--from", f, "--ragged", "truncate")
    assert "ragged 'truncate'" in out


def test_validate_takes_sources_from_home(tmp_path: Path):
    text = (EXAMPLE / "scenario.yaml").read_text(encoding="utf-8")
    template = (ROOT / "examples" / "templates" / "synthetic.pptx").as_posix()
    scenario = tmp_path / "сценарий.yaml"  # рядом нет sources.yaml
    scenario.write_text(text.replace("../templates/synthetic.pptx", template), encoding="utf-8")
    r = runner.invoke(app, ["validate", str(scenario)])
    assert r.exit_code == 1 and "не описан" in r.output
    agen("source", "import", EXAMPLE / "sources.yaml")
    assert "Сценарий в порядке" in agen("validate", scenario)
    r = runner.invoke(app, ["validate", str(scenario), "--no-home"])
    assert r.exit_code == 1
