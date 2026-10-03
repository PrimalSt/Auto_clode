# storage — папка данных, метаданные, файлы загрузок

Реализации хранилищ для одного пользователя на компьютере (ARCHITECTURE.md, разделы 4.2
и 9). Модули обработки этот пакет не импортируют: они получают от `worker` манифесты и
пути.

- `DataFolder` — папка данных: по умолчанию `%LOCALAPPDATA%\Autogenerator` в Windows и
  `~/.local/share/autogenerator` в Linux и macOS; другую задают переменной `AGEN_HOME` или
  параметром `agen --home`. Сетевые папки отклоняются, для папок OneDrive, Dropbox и
  SharePoint выдаётся предупреждение.
- `FolderLock` — блокировка папки данных: метаданные пишет один процесс (сейчас — команда
  `agen`, позже — сервер приложения). Вторая команда, пока первая пишет, получает
  сообщение «папка данных занята». Блокировку держит открытый файл `server.lock`, поэтому
  после сбоя она снимается сама.
- `SqliteMetadataStore` — источники, версии их настроек и загрузки, сценарии и их версии,
  шаблоны оформления и их версии (с подтверждёнными ролями макетов), запуски в
  `db/autogenerator.sqlite` (SQLite в режиме WAL, SQLAlchemy 2). Схема создаётся и
  обновляется миграциями Alembic при открытии базы (`migrations/versions`); перед миграцией
  существующей базы делается копия в `backups`. Каждое изменение настроек источника —
  новая версия; `history_manifest` собирает из метаданных манифест истории для `history`.
- `LocalBlobStore` — файлы загрузок: `local/sources/<источник>/uploads/<загрузка>/month=…`.

Раскладка папки:

```
Autogenerator/
  db/autogenerator.sqlite
  local/sources/<источник>/uploads/<загрузка>/month=2026-03/part-0.parquet
  local/sources/<источник>/uploads/<загрузка>/rejects.parquet
  local/themes/<шаблон>/<версия>.pptx
  local/outputs/<запуск>/Отчёт_….pptx
  cache/  tmp/  backups/  logs/  server.lock
```

Новая миграция: опишите изменение в `tables.py` и добавьте файл в `migrations/versions`
с `down_revision` предыдущей (шаблон — `migrations/script.py.mako`).

Тесты модуля: `uv run agen test storage` (или `uv run pytest packages/storage`).
