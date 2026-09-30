# steps-std — встроенные шаги, окна и агрегаты

Плагины движка. Зависят только от `contracts`.

Шаги обработки входа (этап M0):

| Шаг | Параметры | Что делает |
|---|---|---|
| `filter` | `where` | оставляет строки по условию на SQL: `amount > 0 AND region <> 'Прочие'` |
| `dedupe` | `by`, `keep` | удаляет дубликаты по столбцам; `last` — строка из самой новой загрузки |
| `formula` | `column`, `expr` | новый или заменённый столбец: `amount / 1.2` |
| `select` | `columns` | оставляет перечисленные столбцы (столбец периода остаётся всегда) |

Окна (какой отрезок истории берут набор или показатель; P — отчётный период):
`report_period`, `previous_period`, `same_period_last_year`, `quarter_to_date`,
`year_to_date`, `last_n(6)`, `all`, `range(2026-01-01, 2026-03-31)`.

Агрегаты: `sum`, `count`, `count_distinct`, `mean`, `min`, `max`. Сумма пустого множества —
«нет данных», а не 0.

Остальные шаги MVP (`time_filter`, `sort`, `rename`, `cast`, `join`, `sql`, `python`)
появятся на этапе M2.

Тесты модуля: `uv run agen test steps-std` (или `uv run pytest packages/steps-std`).
