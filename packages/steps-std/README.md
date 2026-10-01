# steps-std — встроенные шаги, окна и агрегаты

Плагины движка. Зависят только от `contracts`.

## Шаги обработки входа

Шаги идут сверху вниз по всей истории входа (поэтому дубликаты ищутся и между выгрузками).
У каждого шага есть `id`, `type` и `enabled: false`, чтобы отключить его, не удаляя.
Условия и формулы пишутся на SQL (диалект DuckDB); то, что не переводится в Polars,
выполняет DuckDB.

| Шаг | Параметры | Что делает |
|---|---|---|
| `filter` | `where` или `conditions` + `combine` | строки по условию: `amount > 0 AND region <> 'Прочие'` или по списку условий конструктора (`eq`, `in`, `contains`, `between`, `is_null`, …; `combine: all / any`) |
| `time_filter` | `start`/`end`, `last` + `unit` или `period`; `column` | диапазон дат, последние N месяцев (недель, …) или период `previous_month`, `quarter_to_date`, …, считая от отчётного периода (или от даты запуска — `settings.relative_to`) |
| `dedupe` | `by`, `keep`, `column`, `depth` | дубликаты по столбцам `by` (пусто — по всем): `last` — строка из самой новой загрузки, `first`, `max` / `min` по столбцу `column`; `depth: 3` — искать только в последних трёх периодах |
| `sort` | `by` | порядок строк: `[-amount, date]` |
| `select` | `columns` или `drop` | оставить или убрать столбцы (столбец периода остаётся всегда) |
| `rename` | `columns` | `{old: new}` |
| `cast` | `columns`, `formats` | смена типа: строка → число («1 234,5», с пробелами), дата (`%d.%m.%Y` и другие форматы), логический; нераспознанные значения — в журнал |
| `formula` | `column`, `expr` | новый или заменённый столбец: `amount / 1.2` |
| `join` | `with`, `on`, `how`, `columns`, `suffix` | объединение с другим входом: `on: [order_no]` или `{order_no: number}`, `how: left / inner / full / semi / anti`; строки без пары и повторы ключей во втором входе — в журнал |
| `sql` | `query` | запрос DuckDB к текущей таблице `data` и к другим входам по их `id` |
| `python` | `code`, `mode`, `frame`, … | функция `transform(df, ctx)`, см. ниже |

Шаг «Python» (`mode`):

- `table` — вся таблица (pandas или Polars: `frame`) в отдельном процессе; если таблица не
  поместится в отведённую память, шаг не запускается и предлагает другой режим;
- `lazy` — `pl.LazyFrame`, выполнение остаётся потоковым (для миллионов строк);
- `batches` — порции pandas по 100 тыс. строк; группировки, сдвиги и поиск дубликатов внутри
  порции дают неверный результат, и проверка сценария об этом предупреждает.

`uses` — какие столбцы читает код (иначе — все), `adds` или `output` — какие столбцы и
типы он возвращает (чтобы следующие шаги проверялись без запуска), `timeout`, `cache: false`
— не брать результат из кэша (код читает внешние файлы). Через `ctx` доступны отчётный период
(`ctx.period`), окна (`ctx.window("previous_period")`), `ctx.warn(...)` и `print` в журнал.
Ошибка показывается с номером строки кода.

```yaml
- id: margin
  type: python
  mode: lazy
  adds: { margin: float }
  code: |
    import polars as pl
    def transform(lf, ctx):
        return lf.with_columns(margin=pl.col("amount") - pl.col("cost"))
```

## Окна

Какой отрезок истории берут набор или показатель (P — отчётный период):
`report_period`, `previous_period`, `same_period_last_year`, `quarter_to_date`,
`year_to_date`, `last_n(6)`, `all`, `range(2026-01-01, 2026-03-31)`.

## Агрегаты

`sum`, `count`, `count_distinct`, `mean`, `median`, `min`, `max`, `first`, `last` (первое и
последнее значение по дате, затем по загрузке и строке файла). Сумма пустого множества —
«нет данных», а не 0.

Тесты модуля: `uv run agen test steps-std` (или `uv run pytest packages/steps-std`).
