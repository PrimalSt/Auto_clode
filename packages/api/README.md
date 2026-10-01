# api — фасад для своего кода и Jupyter

Тот же движок, что у приложения:

```python
from autogenerator.api import run

result = run("examples/sales/scenario.yaml", period="2026-03", output="отчёт.pptx")
print(result.output_path, result.warnings)
```

Откуда берётся история входа: файлы, переданные явно (`inputs={"sales": ["jan.csv",
"feb.csv"]}` или `data_dir` — по подпапке на вход), иначе папка данных приложения, если
там есть источник входа, иначе папка `data` рядом со сценарием. Источники по умолчанию —
`sources.yaml` рядом со сценарием (для входов из папки данных — настройки оттуда),
шаблон — `theme` из сценария.

**Папка данных** — `Home`: источники, загрузки, история.

```python
from autogenerator.api import Home

with Home.open(write=True) as home:          # папка приложения или AGEN_HOME
    spec, snapshot = home.draft_source("Продажи_2026-01.csv", "sales")
    home.create_source(spec)
    out = home.upload("sales", "Продажи_2026-01.csv")
    print(out.record.period.key, out.record.rows, out.issues)
    print(home.coverage("sales").gaps)
    home.export_history("sales", "история.parquet")
```

`write=True` берёт блокировку папки данных: пишет метаданные один процесс. Читать можно и
без неё. Тот же файл второй раз не загружается без `force=True`; при правиле «ask» правило
для загрузки передаётся в `overlap_policy` или выбирается в `choose_policy`. Выгрузку из
нескольких файлов загружает `home.upload("orders", ["часть1.xlsx", "часть2.xlsx"])` — одной
загрузкой. У выгрузки-среза период берётся из имени файла или из `period=Period.parse("2026-01")`.

Также: `validate` (проверка без данных), `inspect` (структура файла), `modules`
(плагины и их состояние), `load_scenario`, `load_sources`.

Тесты модуля: `uv run agen test api` (или `uv run pytest packages/api`).
