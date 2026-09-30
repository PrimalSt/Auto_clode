# cli — команда agen

```
uv run agen run examples/sales/scenario.yaml                 # отчёт в текущую папку
uv run agen run сценарий.yaml --period 2026-02 -o отчёт.pptx # за прошлый период
uv run agen run сценарий.yaml -i sales=янв.csv -i sales=фев.csv --workdir отладка
uv run agen validate сценарий.yaml                           # проверка без данных
uv run agen inspect выгрузка.csv                             # как прочитан файл
uv run agen modules                                          # плагины и их состояние
uv run agen test engine                                      # тесты одного модуля
```

Коды выхода `agen run`: 0 — отчёт собран; 2 — собран, но на слайдах есть пометки об
ошибках; 1 — отчёт не собран (сообщение объясняет, что исправить).

CLI работает только через фасад `api` (это проверяет `uv run lint-imports`).

Тесты модуля: `uv run agen test cli` (или `uv run pytest packages/cli`).
