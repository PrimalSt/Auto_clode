"""Обнаружение плагинов в отдельном коротком процессе.

Так сервер и экран «Модули» узнают о плагинах, не загружая их код к себе: зависший или
падающий при импорте плагин ломает только этот процесс (ARCHITECTURE.md, раздел 4.3).
"""

from __future__ import annotations

import os
import subprocess
import sys

from autogenerator.contracts import PluginManifest


def discover_manifest_isolated(timeout: float = 60.0) -> PluginManifest:
    """Запустить ``python -m autogenerator.plugin_host --json`` и прочитать манифест."""
    cmd = [sys.executable, "-m", "autogenerator.plugin_host", "--json"]
    # В Windows вывод в канал идёт в кодировке системы (cp1251 или cp1252), а в манифесте —
    # русский текст: просим дочерний процесс писать в UTF-8.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, encoding="utf-8", env=env)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Обнаружение плагинов не уложилось в {timeout:.0f} с") from e
    if proc.returncode != 0:
        raise RuntimeError(f"Обнаружение плагинов завершилось с ошибкой:\n{proc.stderr[-4000:]}")
    return PluginManifest.model_validate_json(proc.stdout)
