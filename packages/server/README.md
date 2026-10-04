# server — локальный сервер приложения

REST API и поток событий для окна (ARCHITECTURE.md, разделы 6.8, 10 и 12). Сервер держит
папку данных на запись, пока работает, и единственный пишет метаданные. Модули обработки и
плагины он не импортирует: работу делают три процесса-исполнителя (`runner.ProcessExecutor`):
`main` — загрузки, запуски, импорт шаблонов; `preview` — превью узла и пробная сборка слайда;
`light` — проверка и сохранение сценария, манифест модулей, мелкие вызовы (они не ждут ни
отчёт, ни картинку слайда). Ошибка в модуле обработки или плагине ломает вызов, а не сервер.
Исполнитель, в котором выполнялся код пользователя в режиме `lazy`, после вызова
перезапускается.

## Запуск

```
uv run agen serve --dev                    # сервер без окна; печатает токен и адрес
python -m autogenerator.server --home D:\data --parent 1234
```

- Слушает только `127.0.0.1`, порт по умолчанию — случайный свободный (`--port`).
- Когда готов, печатает `AGEN_SERVER_READY {"port": …, "pid": …, "url": …}`; если не
  запустился — `AGEN_SERVER_ERROR {"code": …, "message": …}` и код выхода 3 (например,
  `data_folder_locked`: папку держит другой сервер или команда `agen`; `port_busy`: порт из
  `--port` занят).
- Токен — из переменной `AGEN_TOKEN` (её задаёт оболочка) или новый. В папке данных на время
  работы лежат `server.json` (порт, номер процесса) и `cli.token` (токен, только для
  текущего пользователя).
- `--parent` — номер процесса оболочки: если он пропал, сервер останавливается сам.
- `--dev` — режим разработчика: страницы `/docs` и `/openapi.json` без токена (вне режима
  разработчика их нет) и проверка изменённых модулей (ниже).
- `--origin` — разрешить запросы из браузера со страницы другого адреса (CORS). Окну не нужен:
  оно открывается с адреса сервера, а dev-сервер интерфейса проксирует `/api`.
- Журнал — `logs/server.log` в папке данных.
- `AGEN_WINDOWLESS=1` (задаёт оболочка): в Windows исполнители установленного приложения
  запускаются через `pythonw.exe`, а тесты модулей — без окна, чтобы у программы без консоли
  не всплывали консольные окна.

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
| Модули | `GET /api/modules`, `POST /api/modules/restart`, `POST /api/modules/check` (задание, режим разработчика), `POST /api/modules/cache/clear` |
| Источники | `GET/POST /api/sources`, `POST /api/sources/draft` (задание), `POST /api/sources/import`, `GET/PUT/DELETE /api/sources/{id}`, `…/versions`, `…/usage`, `…/history` |
| Загрузки | `GET/POST /api/sources/{id}/uploads` (задание), `GET/PATCH/DELETE /api/uploads/{id}` |
| Сценарии | `GET/POST /api/scenarios`, `GET/PUT/DELETE /api/scenarios/{id}`, `…/versions`, `GET/PUT …/yaml`, `…/validate`, `…/copy` |
| Запуски | `POST /api/scenarios/{id}/runs` (задание), `GET /api/runs`, `GET/DELETE /api/runs/{id}`, `…/output`, `POST …/rerun` (задание) |
| Оформление | `GET/POST /api/themes` (задание), `GET/DELETE /api/themes/{id}`, `…/versions`, `…/versions/{n}`, `POST …/reimport` (задание), `PUT …/roles`, `POST …/export` |
| Превью | `POST /api/preview/validate`, `POST /api/preview/node` и `/slide` (задания очереди `preview`), `GET /api/preview/files/{id}/slide.pptx` и `slide.png` |
| Задания и события | `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `GET /api/events` |

Загрузка останавливается на выборе, если он нужен: `schema_review` (столбец пропал, в файле
есть похожий; в `details.files` — сверка по файлам с кандидатами) или `overlap_choice`
(период уже загружен). Окно показывает выбор и отправляет загрузку снова — с `mapping` /
`declined` / `accept_mapping` или с `overlap_policy`.

Поток событий `GET /api/events` (Server-Sent Events): `job` — ход, итог и ошибка заданий
(без самого итога), `changed` — данные раздела изменились (`{"what": "sources" | "uploads" |
"scenarios" | "themes" | "runs" | "all", "id": …}`). После переподключения окно получает
пропущенные события (заголовок `Last-Event-ID` или `?after=`).

**Режим разработчика** (`--dev`, сервер установлен из копии исходников через `uv sync`):
`POST /api/modules/check` с `{"modules": [...]}` или `{}` — тесты модулей (`packages/<модуль>/tests`,
по умолчанию — модулей, в коде которых есть файлы новее запуска сервера или прошлого
применения). Если все прошли, исполнители перезапускаются и считают с новым кодом
(`applied`); если идут задания, новый код не применяется (`note`). Модули самого сервера
(`contracts`, `storage`, `home`, `runner`, `server`) начинают работать после перезапуска
приложения (`restart_app`). Итог — `ModulesCheckOut`. Вне режима разработчика — `501`.

Окно сохраняет сценарий только с id шаблона из папки данных: шаблон загружается в разделе
«Оформление» (`POST /api/themes`).

Тесты модуля: `uv run agen test server` (или `uv run pytest packages/server`).
