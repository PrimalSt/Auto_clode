"""Режим разработчика (ARCHITECTURE.md, раздел 5.3; PRD F-809).

Сервер запущен из копии исходников (пакеты установлены из неё, `uv sync`). Изменённый модуль
сначала проверяется своими тестами (`agen test <модуль>`), и только если они прошли,
исполнители перезапускаются и подхватывают новый код. Модули самого сервера (`SERVER_SIDE`)
загружены в процесс сервера: их новый код начинает работать после перезапуска приложения.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel

from autogenerator.contracts import JobContext

SERVER_SIDE = frozenset({"contracts", "storage", "home", "runner", "server"})
"""Модули, которые работают в процессе сервера, а не в исполнителях."""
TEST_TIMEOUT = 30 * 60
OUTPUT_LIMIT = 8000
"""Сколько последних символов вывода тестов показывать."""


class ModuleCheck(BaseModel):
    module: str
    ok: bool
    seconds: float
    output: str
    """Конец вывода pytest (или «тестов нет»)."""


class ModulesCheckOut(BaseModel):
    source: str
    modules: list[ModuleCheck]
    applied: bool
    """Тесты прошли, исполнители перезапущены с новым кодом."""
    restart_app: bool
    """Изменились модули сервера: их код заработает после перезапуска приложения."""
    note: str | None = None
    """Почему новый код не применён, хотя тесты прошли (например, идут задания)."""


def source_root(start: Path | None = None) -> Path | None:
    """Корень копии исходников, из которой установлен сервер, или None (установленное приложение)."""
    here = (start or Path(__file__)).resolve()
    for p in here.parents:
        if (p / "pyproject.toml").is_file() and (p / "packages" / "server").is_dir():
            return p
    return None


def changed_modules(root: Path, since: float) -> list[str]:
    """Модули (папки ``packages/*``), в коде которых есть файлы новее ``since``."""
    out = []
    for pkg in sorted(p for p in (root / "packages").iterdir() if p.is_dir()):
        src = pkg / "src"
        if src.is_dir() and any(f.stat().st_mtime > since for f in src.rglob("*.py")):
            out.append(pkg.name)
    return out


def test_module(root: Path, module: str) -> ModuleCheck:
    """Тесты одного модуля отдельным процессом, как `agen test <модуль>`."""
    tests = root / "packages" / module / "tests"
    if not tests.is_dir():
        return ModuleCheck(module=module, ok=True, seconds=0.0, output="тестов нет")
    t0 = time.monotonic()
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW  # у сервера под оболочкой нет консоли: без окна у pytest
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests), "-q", "-p", "no:cacheprovider"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT,
            creationflags=flags,
        )
        ok, output = proc.returncode == 0, (proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        ok, output = False, f"Тесты не уложились в {TEST_TIMEOUT // 60} мин"
    return ModuleCheck(module=module, ok=ok, seconds=round(time.monotonic() - t0, 1), output=output[-OUTPUT_LIMIT:])


def check_modules(root: Path, modules: list[str], ctx: JobContext | None = None) -> list[ModuleCheck]:
    """Тесты модулей по очереди; первый провал не останавливает остальные (видно всё сразу)."""
    out = []
    for i, m in enumerate(modules):
        if ctx is not None:
            if ctx.cancelled():
                break
            ctx.progress(f"Тесты модуля {m}", i, len(modules), "modules")
        out.append(test_module(root, m))
    return out
