# plugin_host — реестр плагинов

Находит плагины по entry points (`autogenerator.readers`, `.steps`, `.windows`,
`.aggregations`, `.blocks`), загружает их по одному и проверяет версию API. Если плагин
не импортируется, написан для другой версии API или не того вида, неисправным
помечается только он: остальные работают, а ошибка видна в `agen modules`.

Зависит только от `contracts`.

```python
from autogenerator.plugin_host import PluginRegistry

reg = PluginRegistry.discover()          # все установленные плагины
reg.aggregation("sum").sql("amount")     # 'SUM(amount)'
reg.manifest().broken()                  # неисправные плагины с текстом ошибки

# Для тестов модуля — реестр из своих классов, без установки:
reg = PluginRegistry.from_plugins([MyStep])
```

Запуск отдельно: `uv run python -m autogenerator.plugin_host` (или `--json`) печатает
манифест. `discover_manifest_isolated()` делает то же в отдельном процессе: так сервер
(этап M4) узнаёт о плагинах, не загружая их к себе.

Тесты модуля: `uv run agen test plugin_host` (или `uv run pytest packages/plugin_host`).
