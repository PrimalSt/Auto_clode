# runner — очередь заданий, события, процессы-исполнители

Локальные реализации интерфейсов из `contracts.runtime` (ARCHITECTURE.md, раздел 6.6).
Пакет зависит только от `contracts`.

- `ProcessExecutor` — исполнитель в отдельном процессе. Процесс импортирует модуль
  исполнителя по имени (`autogenerator.worker`) и выполняет вызовы по одному:
  `executor.call("ingest_upload", (req,), progress=…, cancelled=…)`. Аргументы и итог — через
  pickle, ход работы — сообщениями, отмена — общим флагом. Упавший процесс даёт вызову
  ошибку `worker_failed` и запускается заново при следующем вызове; таймаут и отмена,
  которую исполнитель не заметил за `cancel_grace` секунд, завершают процесс. После
  `max_calls` вызовов процесс перезапускается. Если модуль исполнителя не загрузился
  (ошибка в модуле обработки), вызовы получают `worker_failed` с трассировкой, а сервер
  продолжает работать.
- `LocalJobQueue` — очереди заданий с потоком на каждую (`main` — загрузки, запуски,
  импорт шаблонов; `light` — быстрые задания). Задание — функция `fn(ctx)`; `ctx.progress`,
  `ctx.cancelled`, `current_job()` — задание текущего потока. Состояние задания:
  `queued → running → done | failed | cancelled`.
- `LocalEventBus` — поток событий с номерами и буфером последних событий: окно после
  переподключения получает пропущенное (`subscribe(after=номер)`).

```python
from autogenerator.runner import LocalEventBus, LocalJobQueue, ProcessExecutor

bus = LocalEventBus()
jobs = LocalJobQueue(bus)
worker = ProcessExecutor("autogenerator.worker", "main", timeout=1800)
info = jobs.submit("modules", lambda ctx: worker.call("plugin_manifest", (False,)).model_dump(mode="json"))
print(jobs.wait(info.id).status)
```

Тесты модуля: `uv run agen test runner` (или `uv run pytest packages/runner`).
