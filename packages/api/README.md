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

Сопоставление переименованных столбцов (F-602…F-606): нужные столбцы — те, что используют
сохранённые сценарии (`home.column_usage("sales")`). Если такой столбец пропал, а похожий в
файле есть, `upload` останавливается с `SCHEMA_REVIEW`, в `e.details["files"]` — сверка с
кандидатами. Решение передаётся так:

```python
home.upload("sales", "фев.csv", accept_mapping=True)                 # принять предложенное
home.upload("sales", "фев.csv", mapping={"Сумма, руб.": "amount"}, declined=["manager"])
home.upload("sales", "фев.csv", choose_mapping=lambda spec, files: MappingChoice(pairs={...}))
```

Подтверждённые названия запоминаются в `aliases` источника (`out.remembered`).

**Сценарии, шаблоны, запуски** — тоже в папке данных:

```python
with Home.open(write=True) as home:
    saved = home.save_scenario("сценарий.yaml", "sales")    # версия; шаблон-файл загружается сам
    print(saved.record.version, saved.errors)
    home.set_theme_roles("synthetic", {"title_only": "2147483661"})   # новая версия шаблона
    run = home.run_scenario("sales", period="2026-02", output="Отчёты")
    print(run.status, run.output_copy, run.result.environment)
    home.rerun(run.id)                                      # тот же период по текущим данным
    home.backup()                                           # резервная копия базы
```

Также: `scenarios`, `scenario_text` (YAML, как сохранили), `copy_scenario`, `import_theme`,
`export_theme`, `runs`, `run_output`, `backups`, `restore_backup`.

Новая версия шаблона переводит на себя сценарии, которые на ней проверяются без новых
ошибок (`imp.scenarios`); остальные остаются на прежней версии, а `imp.lost` говорит, что
не сходится. `import_theme` без id не загружает уже загруженный файл (`imp.matched`) и не
делает новую версию чужого шаблона с тем же именем файла: тогда нужен явный id.

**Превью** узла — первые строки, число строк до и после каждого шага, набор или показатель:

```python
from autogenerator.api import preview

res = preview("examples/sales/scenario.yaml", "sales/positive_only")   # вход после шага
for st in res.steps:
    print(st.id, st.rows_before, st.rows_after)
res = preview("examples/sales/scenario.yaml", "metric:revenue", period="2026-02")
print(res.metrics)   # revenue, revenue_prev, revenue_prev_change, revenue_prev_change_pct
```

На больших данных превью входа строится по выборке (`res.sample`, `res.approximate`);
`sample=1` — точно, без выборки.

**Слайды и шаблон**:

```python
from autogenerator.api import check_theme, preview_slide, scaffold_theme

m = check_theme("шаблон.pptx")                  # манифест: слайды, метки, графики, замечания
print([str(i) for i in m.lint])
print(scaffold_theme(m))                         # заготовка slides для сценария (YAML)
res = preview_slide("examples/sales/scenario.yaml", 2, image="слайд.png")
print(res.output_path, res.image_path, res.warnings)
```

Также: `describe_theme` (отчёт о шаблоне текстом), `validate` (проверка без данных), `inspect` (структура файла), `modules`
(плагины и их состояние), `load_scenario`, `load_sources`.

Тесты модуля: `uv run agen test api` (или `uv run pytest packages/api`).
