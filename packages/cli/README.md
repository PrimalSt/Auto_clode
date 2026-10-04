# cli — команда agen

Отчёт:

```
uv run agen run examples/sales/scenario.yaml                 # отчёт в текущую папку
uv run agen run сценарий.yaml --period 2026-02 -o отчёт.pptx # за прошлый период
uv run agen run сценарий.yaml -i sales=янв.csv -i sales=фев.csv --workdir отладка
uv run agen run сценарий.yaml --no-home                      # только файлы, без папки данных
uv run agen validate сценарий.yaml                           # проверка без данных; источники — и из папки данных
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

Слайды и шаблон:

```
uv run agen theme check шаблон.pptx --verbose                # слайды, метки, графики, замечания
uv run agen theme check шаблон.pptx --layouts --json         # макеты и роли; JSON для своего кода
uv run agen theme scaffold шаблон.pptx -o слайды.yaml        # заготовка слайдов для сценария
uv run agen preview сценарий.yaml slide:3 --image слайд.png  # пробная сборка одного слайда
```

`agen theme check` возвращает код 1, если в шаблоне есть ошибки. Пробная сборка слайда
пишет `превью_слайда_N.pptx` (или `-o`), непривязанные метки остаются на виду.

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
uv run agen upload add orders выгрузка.csv --ragged truncate # строки длиннее шапки: отбросить лишние поля
uv run agen upload add clients выгрузка.xlsx --period 2026-01 # период среза, если его нет в имени
uv run agen upload list sales
uv run agen upload show <id>                                 # ошибки приведения, профиль
uv run agen upload accept <id>                               # принять загрузку «на проверке»
uv run agen upload exclude <id> / include <id> / delete <id>
uv run agen upload period <id> 2026-03-01..2026-03-31        # поправить период загрузки
uv run agen history sales                                    # загрузки, покрытие, пропуски
uv run agen history sales --export история.parquet           # действующая история в файл
```

Переименованные столбцы: если в файле нет столбца, который нужен сохранённым сценариям
(или, пока сценариев нет, любого столбца источника), а похожий есть, загрузка ждёт решения.
В консоли команда спрашивает сама (для столбца, нужного сценариям, «0» — отменить
загрузку: без него отчёт не соберётся); без консоли — останавливается и подсказывает:

```
uv run agen upload add sales фев.csv --accept-mapping        # принять предложенное сопоставление
uv run agen upload add sales фев.csv --map "Сумма, руб.=amount"   # своё: название в файле=id
uv run agen upload add sales фев.csv --empty manager         # оставить столбец пустым в этой загрузке
```

Подтверждённое название запоминается в `aliases` источника (новая версия настроек), и в
следующий раз сопоставится само.

Если новая загрузка заменяет прежнюю за тот же период (правило `replace_period`), команда
так и пишет: «Заменяет загрузку #1 … за тот же период» (загрузка на проверке — «После
принятия заменит …»). Когда у прежней загрузки те же столбцы, она подсказывает `--concat`:
части одной выгрузки, загруженные отдельными командами, заменяют друг друга, а не
складываются.

Сценарии, шаблоны и запуски в папке данных:

```
uv run agen scenario add сценарий.yaml --id sales            # сохранить; повторно — новая версия
uv run agen scenario list / show sales / validate sales
uv run agen scenario export sales -o сценарий.yaml           # текст, как сохранили (с комментариями)
uv run agen scenario copy sales sales_q --name "Квартальный"
uv run agen run sales -o Отчёты                              # отчёт в папке данных и копия в Отчёты
uv run agen run sales --period 2026-01                       # пересборка за прошлый период
uv run agen runs list / show sales-003 -v / open sales-003   # история запусков и журнал
uv run agen runs rerun sales-001                             # тот же период по текущим данным
uv run agen theme import шаблон.pptx --id corp               # шаблон в папку данных; с тем же --id — версия
uv run agen theme list / versions corp / check corp
uv run agen theme roles corp title_only=2147483661           # подтвердить роль макета
uv run agen theme export corp -o .                           # доработать в PowerPoint и загрузить снова
uv run agen backup create / list / restore <файл>            # резервная копия базы
```

Шаблон, заданный в сценарии файлом, при `scenario add` загружается в папку данных сам; в
сценарии остаётся id шаблона. Файл, который уже загружен (в любой шаблон), второй раз не
загружается. Новый файл получает id по имени; если шаблон с таким id уже есть, `theme import`
просит указать `--id` (новая версия этого шаблона или другой шаблон), а `scenario add`
заводит отдельный шаблон «id-2», если только это не файл того же сценария (тогда это новая
версия его шаблона). Новая версия шаблона (импорт или подтверждение ролей) даёт новую версию
сценариям на нём; сценарий, у которого на ней появились ошибки, остаётся на прежней версии
шаблона, и команда перечисляет, что не сходится (код выхода 2). `agen run <id>` пишет запуск
в историю: версии сценария, источников, шаблона, приложения и плагинов, состав истории,
строки после каждого узла.

Прочее:

```
uv run agen modules                                          # плагины и их состояние
uv run agen serve --dev                                      # сервер приложения без окна (для интерфейса и API)
uv run agen test engine                                      # тесты одного модуля
uv run agen test worker --template шаблон.pptx               # приёмочный тест своего шаблона
```

`agen run` предупреждает, если в загрузке, которую читает отчёт, не было нужного сценарию
столбца (например, его переименовали в выгрузке): «[input:sales] в загрузке #4 «…» (2026-03)
нет столбца «Регион» (region) — в ней он пустой, а сценарий его использует (…)». Новое
название — в `aliases` источника, затем `agen upload add … --force`: по правилу
`replace_period` новая загрузка заменит прежнюю, при других правилах прежнюю исключите
(`agen upload exclude <id>`). `agen validate` этого не проверяет: он смотрит сценарий без
данных и без отчётного периода.

Коды выхода `agen run`: 0 — отчёт собран; 2 — собран, но на слайдах есть пометки об
ошибках; 1 — отчёт не собран (сообщение объясняет, что исправить).

CLI работает только через фасад `api` (это проверяет `uv run lint-imports`). Метаданные
папки данных пишет сама команда, взяв блокировку папки: вторая команда, которая пишет, в
это время получит сообщение «папка данных занята». Пока работает сервер приложения
(`agen serve` или открытое окно), он держит папку данных, и команды, которые в неё пишут,
получают то же сообщение; команды чтения работают. `agen serve` запускает сервер отдельным
процессом (`python -m autogenerator.server`), сам CLI сервер не импортирует.

Тесты модуля: `uv run agen test cli` (или `uv run pytest packages/cli`).
