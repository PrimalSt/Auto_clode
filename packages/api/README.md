# api — фасад для своего кода и Jupyter

Тот же движок, что у приложения, одной функцией:

```python
from autogenerator.api import run

result = run("examples/sales/scenario.yaml", period="2026-03", output="отчёт.pptx")
print(result.output_path, result.warnings)
```

Выгрузки можно передать явно (`inputs={"sales": ["jan.csv", "feb.csv"]}`) или положить в
папку данных: по подпапке на вход, файлы загружаются в порядке имён. Источники по
умолчанию — `sources.yaml` рядом со сценарием, шаблон — `theme` из сценария.

Также: `validate` (проверка без данных), `inspect` (структура файла), `modules`
(плагины и их состояние), `load_scenario`, `load_sources`.

Тесты модуля: `uv run agen test api` (или `uv run pytest packages/api`).
