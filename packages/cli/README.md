# cli — команда agen

Отчёт:

```
uv run agen run examples/sales/scenario.yaml                 # отчёт в текущую папку
uv run agen run сценарий.yaml --period 2026-02 -o отчёт.pptx # за прошлый период
uv run agen run сценарий.yaml -i sales=янв.csv -i sales=фев.csv --workdir отладка
uv run agen run сценарий.yaml --no-home                      # только файлы, без папки данных
uv run agen validate сценарий.yaml                           # проверка без данных
```

Превью узла сценария — первые строки и число строк до и после каждого шага, набор или
значение показателя:

```
uv run agen preview examples/sales/scenario.yaml sales/positive_only   # вход после шага
uv run agen preview examples/sales/scenario.yaml dataset:plan_fact
uv run agen preview examples/sales/scenario.yaml metric:revenue --period 2026-02
uv run agen preview сценарий.yaml sales --sample off                   # точно, без выборки
uv run agen preview сценарий.yaml sales --json                         # для своего кода
```

На больших данных превью входа строится по выборке (`--sample auto`), числа строк тогда
помечены «≈». Код выхода 1 — в узле или в том, от чего он зависит, есть ошибка.

Выгрузки и история (папка данных — `--home`, переменная `AGEN_HOME` или папка приложения):

```
uv run agen inspect выгрузка.csv --preview 5                 # как прочитан файл, типы, профиль
uv run agen source create sales --from выгрузка.csv          # источник по первой выгрузке
uv run agen source create sales --from выгрузка.csv --yaml sales.yaml   # только черновик
uv run agen source create clients --from Клиенты.xlsx --period-at-upload   # срез: период при загрузке
uv run agen source import sources.yaml                       # из YAML; изменения — новой версией
uv run agen source export sales -o sales.yaml                # настройки в YAML
uv run agen source set sales --overlap ask --keys order_no   # быстрые правки
uv run agen source show sales --versions
uv run agen upload add sales янв.csv фев.csv                 # загрузка с прогрессом
uv run agen upload add sales мар.csv --overlap append        # правило, если у источника «ask»
uv run agen upload add orders ч1.xlsx ч2.xlsx --concat       # части одной выгрузки — одна загрузка
uv run agen upload add clients выгрузка.xlsx --period 2026-01 # период среза, если его нет в имени
uv run agen upload list sales
uv run agen upload show <id>                                 # ошибки приведения, профиль
uv run agen upload accept <id>                               # принять загрузку «на проверке»
uv run agen upload exclude <id> / include <id> / delete <id>
uv run agen upload period <id> 2026-03-01..2026-03-31        # поправить период загрузки
uv run agen history sales                                    # загрузки, покрытие, пропуски
uv run agen history sales --export история.parquet           # действующая история в файл
```

Прочее:

```
uv run agen modules                                          # плагины и их состояние
uv run agen test engine                                      # тесты одного модуля
```

Коды выхода `agen run`: 0 — отчёт собран; 2 — собран, но на слайдах есть пометки об
ошибках; 1 — отчёт не собран (сообщение объясняет, что исправить).

CLI работает только через фасад `api` (это проверяет `uv run lint-imports`). Пока нет
приложения, метаданные папки данных пишет сама команда, взяв блокировку папки: вторая
команда, которая пишет, в это время получит сообщение «папка данных занята».

Тесты модуля: `uv run agen test cli` (или `uv run pytest packages/cli`).
