# blocks-std — встроенные блоки слайдов

Плагины `render`. Зависят только от `contracts`.

- `text` — текст с переменными (Jinja2 в песочнице): `{{ metrics.revenue | money(scale='million', decimals=1) }}`,
  `{{ period.label }}`, `{{ change_pct(metrics.revenue, metrics.revenue_prev) | percent(sign=true) }}`.
  Пустое значение печатается как «нет данных», неизвестная переменная — ошибка блока.
- `chart` — «родная» диаграмма PowerPoint: `column`, `stacked_column`, `bar`, `stacked_bar`,
  `line`, `pie`, `doughnut`. Данные встраиваются, график можно править в PowerPoint.
  Даты в категориях — по-русски («янв. 2026»); столбцы и полосы начинаются от нуля.
- `table` — таблица PowerPoint с русскими форматами чисел, процентов и дат;
  пустое значение — «—».

Форматы (`formats.py`): `1 234 568`, `26,2 млн ₽`, `12,5%`, `+1,2 п.п.`, «март 2026»,
«I квартал 2026», «3–19 марта 2026», месяцы в падежах (`period.month_prep` — «марте»).
Разряды и единицы отделяются неразрывным пробелом.

Тесты модуля: `uv run agen test blocks-std` (или `uv run pytest packages/blocks-std`).
