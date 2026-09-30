# Autogenerator

Автогенератор отчётных презентаций: выгрузки CSV и Excel за несколько периодов,
логика обработки и слайдов — и готовая .pptx в корпоративном шаблоне. Что и зачем —
в [PRD.md](PRD.md), как устроено — в [ARCHITECTURE.md](ARCHITECTURE.md).

Сейчас готов этап **M0 — сквозной каркас**: движок без интерфейса. Команда `agen run`
собирает отчёт из примеров в `examples/`.

## Как запустить

Нужен [uv](https://docs.astral.sh/uv/) (он сам поставит Python 3.12).

```
uv sync
uv run agen run examples/sales/scenario.yaml
```

В текущей папке появится `Отчёт_продажи_2026-03.pptx`: шесть слайдов за март 2026 —
итоги с планом, динамика квартала, регионы, квартал, лучшие менеджеры.

Пример в `examples/sales/` устроен как настоящие выгрузки:

- `data/sales/` — продажи за январь, февраль и март (CSV в Windows-1251 с «;»);
  в феврале столбцы переименованы, в марте переставлены и добавлен лишний, есть
  дубликаты заказов и возвраты;
- `data/plan/` — план на квартал (Excel, заголовок отчёта над шапкой);
- `sources.yaml` — источники: столбцы, их типы и другие названия;
- `scenario.yaml` — сценарий: обработка, наборы данных, показатели и слайды.

Шаблон `examples/templates/synthetic.pptx` синтетический. Данные и шаблон создаёт
`uv run python tools/make_examples.py`. Корпоративный шаблон в репозиторий не кладётся.

Свой шаблон можно посмотреть глазами приложения, не меняя его:

```
uv run python -m autogenerator.theme "путь\к\шаблону.pptx" --layouts
```

## Модули

Каждый модуль — отдельный пакет в `packages/` со своим README, тестами и запуском
без остального приложения (`python -m autogenerator.<модуль>`). Модули обработки не
импортируют друг друга, плагины знают только контракты; это проверяет `uv run lint-imports`
(правила — в `pyproject.toml`, раздел `[tool.importlinter]`).

| Модуль | Что делает |
|---|---|
| [contracts](packages/contracts) | модели и интерфейсы, общие для всех |
| [plugin_host](packages/plugin_host) | находит и загружает плагины, изолирует сломанные |
| [readers-std](packages/readers-std) | плагины: чтение CSV и Excel |
| [ingest](packages/ingest) | структура файла, распознавание типов, запись загрузки |
| [schema](packages/schema) | сверка столбцов нового файла с источником |
| [history](packages/history) | история загрузок, правила пересечения, периоды |
| [steps-std](packages/steps-std) | плагины: шаги обработки, окна, агрегаты |
| [engine](packages/engine) | граф сценария: обработка, наборы, показатели |
| [theme](packages/theme) | импорт шаблона: макеты, роли, слайды с метками |
| [blocks-std](packages/blocks-std) | плагины: текст, график, таблица |
| [render](packages/render) | сборка .pptx |
| [worker](packages/worker) | соединяет модули в задание «собрать отчёт» |
| [api](packages/api) | фасад для своего кода и Jupyter |
| [cli](packages/cli) | команда `agen` |

Разбирать модуль удобно на промежуточных данных настоящего запуска:

```
uv run agen run examples/sales/scenario.yaml --workdir отладка
uv run python -m autogenerator.history отладка/manifests/sales.json
uv run python -m autogenerator.render examples/sales/scenario.yaml --theme отладка/theme/manifest.json --data отладка/engine/outputs --period 2026-03 -o снова.pptx
```

## Проверки

```
uv run pytest            # все тесты
uv run agen test engine  # тесты одного модуля
uv run ruff check .      # стиль
uv run mypy              # типы
uv run lint-imports      # границы модулей
```

Те же проверки идут в CI на Ubuntu и Windows.

## Что дальше

- **M1. Данные** — большие CSV потоком, Excel до 1 млн строк, профиль данных, типы периодов.
- **M2. Обработка** — остальные шаги, SQL и Python, объединение источников, превью.
- **M3. Слайды** — конструктор, слайды-образцы корпоративного шаблона с метками, графиками
  и таблицами.
- **M4. Приложение** — десктоп-программа, журнал, история запусков, установщик.
