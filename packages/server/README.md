# server — локальный сервер приложения

REST API и поток событий для окна (ARCHITECTURE.md, разделы 6.8, 10 и 12). Сервер держит
папку данных на запись, пока работает, и единственный пишет метаданные. Модули обработки и
плагины он не импортирует: работу делают два процесса-исполнителя (`runner.ProcessExecutor`),
`main` — загрузки, запуски, импорт шаблонов, `light` — проверка сценария, превью, мелкие
вызовы. Ошибка в модуле обработки или плагине ломает вызов, а не сервер.

## Запуск

```
uv run agen serve --dev                    # сервер без окна; печатает токен и адрес
python -m autogenerator.server --home D:\data --parent 1234 --origin http://tauri.localhost
```

- Слушает только `127.0.0.1`, порт по умолчанию — случайный свободный (`--port`).
- Когда готов, печатает `AGEN_SERVER_READY {"port": …, "pid": …, "url": …}`; если не
  запустился — `AGEN_SERVER_ERROR {"code": …, "message": …}` и код выхода 3 (например,
  `data_folder_locked`: папку держит другой сервер или команда `agen`).
- Токен — из переменной `AGEN_TOKEN` (её задаёт оболочка) или новый. В папке данных на время
  работы лежат `server.json` (порт, номер процесса) и `cli.token` (токен, только для
  текущего пользователя).
- `--parent` — номер процесса оболочки: если он пропал, сервер останавливается сам.
- `--origin` — origin окна для CORS; `--dev` — страница `/docs` без токена.
- Журнал — `logs/server.log` в папке данных.

## Доступ

Токен — заголовком `Authorization: Bearer …` (или `X-Agen-Token`); запросам `GET` — ещё и
параметром `?token=` (поток событий через `EventSource`, картинки слайдов в `<img>`).
Заголовок `Host` должен быть `127.0.0.1` или `localhost`. Без токена отвечает только
`GET /api/health`.

## API

Ошибки — `{"code", "message", "hint", "details"}`, код — из `ErrorCode`, HTTP-статус — по коду
(`not_found` 404, `already_exists` и `schema_review` 409, …).

Долгие действия — задания: ответ `202` с заданием (`JobInfo`), ход — событиями `job`, итог —
`GET /api/jobs/{id}`. Параметр `?wait=N` ждёт конца задания до N секунд (не больше 300).
Отменить — `POST /api/jobs/{id}/cancel`.

| Раздел | Пути |
|---|---|
| Система | `GET /api/health`, `GET /api/system`, `POST /api/system/backup`, `GET /api/system/backups`, `POST /api/system/restore`, `POST /api/system/shutdown` |
| Модули | `GET /api/modules`, `POST /api/modules/restart`, `POST /api/modules/cache/clear` |
| Источники | `GET/POST /api/sources`, `POST /api/sources/draft` (задание), `POST /api/sources/import`, `GET/PUT/DELETE /api/sources/{id}`, `…/versions`, `…/usage`, `…/history` |
| Загрузки | `GET/POST /api/sources/{id}/uploads` (задание), `GET/PATCH/DELETE /api/uploads/{id}` |
| Сценарии | `GET/POST /api/scenarios`, `GET/PUT/DELETE /api/scenarios/{id}`, `…/versions`, `GET/PUT …/yaml`, `…/validate`, `…/copy` |
| Запуски | `POST /api/scenarios/{id}/runs` (задание), `GET /api/runs`, `GET/DELETE /api/runs/{id}`, `…/output`, `POST …/rerun` (задание) |
| Оформление | `GET/POST /api/themes` (задание), `GET/DELETE /api/themes/{id}`, `…/versions`, `…/versions/{n}`, `POST …/reimport` (задание), `PUT …/roles`, `POST …/export` |
| Превью | `POST /api/preview/validate`, `POST /api/preview/node` и `/slide` (задания очереди `light`), `GET /api/preview/files/{id}/slide.pptx` и `slide.png` |
| Задания и события | `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `GET /api/events` |

Загрузка останавливается на выборе, если он нужен: `schema_review` (столбец пропал, в файле
есть похожий; в `details.files` — сверка по файлам с кандидатами) или `overlap_choice`
(период уже загружен). Окно показывает выбор и отправляет загрузку снова — с `mapping` /
`declined` / `accept_mapping` или с `overlap_policy`.

Поток событий `GET /api/events` (Server-Sent Events): `job` — ход, итог и ошибка заданий
(без самого итога), `changed` — данные раздела изменились (`{"what": "sources" | "uploads" |
"scenarios" | "themes" | "runs" | "all", "id": …}`). После переподключения окно получает
пропущенные события (заголовок `Last-Event-ID` или `?after=`).

Окно сохраняет сценарий только с id шаблона из папки данных: шаблон загружается в разделе
«Оформление» (`POST /api/themes`).

Тесты модуля: `uv run agen test server` (или `uv run pytest packages/server`).
