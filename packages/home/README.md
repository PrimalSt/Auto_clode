# home — папка данных приложения

Логика папки данных без модулей обработки: источники, загрузки и история, сопоставление
столбцов, сценарии и шаблоны с версиями, запуски, резервные копии, черновики сценария для
превью. Метаданные пишет `storage` (SQLite), тяжёлую работу делает исполнитель — объект с
функциями `WorkerApi` (`workers.py`).

Зависит только от `contracts` и `storage`, поэтому его открывает и сервер приложения, который
модули обработки не импортирует (правило 2 в ARCHITECTURE.md, раздел 4.2):

- в своём коде и в CLI — `autogenerator.api.Home`: исполнитель — модуль `autogenerator.worker`
  в том же процессе;
- в сервере — `autogenerator.home.Home` с исполнителем `server.workers.RemoteWorker`: каждая
  функция вызывается в процессе-исполнителе (`runner.ProcessExecutor`).

```python
from autogenerator.home import Home

with Home.open(write=True, worker=my_worker, owner="приложение") as home:
    home.upload("sales", "Продажи_2026-03.csv")
    run = home.run_scenario("sales_report")
```

`Home.open(write=True)` берёт блокировку папки данных (`server.lock`) до `close`; `owner` —
кто её держит (это увидит второй процесс в сообщении о занятой папке).

Части:

- `home.py` — источники, загрузки, история, сопоставление при загрузке;
- `library.py` — сценарии и шаблоны оформления, их версии;
- `runs.py` — запуски, пересборка, резервные копии и восстановление;
- `drafts.py` — несохранённый черновик: проверка, превью узла, пробная сборка слайда
  (`tmp/preview/<id>/slide.pptx` и `slide.png`, хранятся последние 20);
- `workers.py` — `WorkerApi`: что нужно от исполнителя.

Тесты — в `packages/api/tests` (через `autogenerator.api.Home`) и `packages/server/tests`
(через сервер и процессы-исполнители).
