"""Режим разработчика (ARCHITECTURE.md, раздел 5.3; PRD F-809).

Сервер запущен из копии исходников (пакеты установлены из неё, `uv sync`). Изменённый модуль
сначала проверяется своими тестами (`packages/<модуль>/tests` отдельным процессом pytest), и
только если они прошли, исполнители перезапускаются и подхватывают новый код. Перезапущенный
исполнитель загружает весь код с диска, поэтому новый код применяется, только когда проверены
все изменённые модули. Модули самого сервера (`SERVER_SIDE`) загружены в процесс сервера: их
новый код начинает работать после перезапуска приложения.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from autogenerator.contracts import AgenError, ErrorCode, JobContext

SERVER_SIDE = frozenset({"contracts", "storage", "home", "runner", "server"})
"""Модули, которые работают в процессе сервера, а не в исполнителях."""
TEST_TIMEOUT = 30 * 60
OUTPUT_LIMIT = 8000
"""Сколько последних символов вывода тестов показывать."""
POLL = 0.5
"""Как часто идущие тесты проверяют отмену (с)."""


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
    """Почему новый код не применён, хотя тесты прошли (идут задания, изменены и другие модули)."""


def source_root(start: Path | None = None) -> Path | None:
    """Корень копии исходников, из которой установлен сервер, или None (установленное приложение)."""
    here = (start or Path(__file__)).resolve()
    for p in here.parents:
        if (p / "pyproject.toml").is_file() and (p / "packages" / "server").is_dir():
            return p
    return None


def known_modules(root: Path) -> list[str]:
    """Модули копии исходников — папки ``packages/*``."""
    return sorted(p.name for p in (root / "packages").iterdir() if p.is_dir())


def resolve_modules(root: Path, names: list[str]) -> tuple[list[str], list[str]]:
    """Имена модулей → папки ``packages/*`` (``_`` и ``-`` взаимозаменяемы, как у `agen test`).
    Возвращает найденные (без повторов, в порядке запроса) и неизвестные. Путь вместо имени
    (``..``, ``C:\\…``) неизвестен: имя сверяется со списком папок, а не открывается как путь."""
    known = set(known_modules(root))
    found: list[str] = []
    unknown: list[str] = []
    for name in names:
        variants = (name, name.replace("_", "-"), name.replace("-", "_"))
        hit = next((v for v in variants if v in known), None)
        if hit is None:
            unknown.append(name)
        elif hit not in found:
            found.append(hit)
    return found, unknown


def changed_modules(root: Path, since: float) -> list[str]:
    """Модули (папки ``packages/*``), в коде которых есть файлы новее ``since``."""
    out = []
    for pkg in sorted(p for p in (root / "packages").iterdir() if p.is_dir()):
        src = pkg / "src"
        if src.is_dir() and any(f.stat().st_mtime > since for f in src.rglob("*.py")):
            out.append(pkg.name)
    return out


def _cancelled() -> AgenError:
    return AgenError(ErrorCode.CANCELLED, "Проверка модулей отменена: новый код не применён")


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Завершить pytest вместе с процессами, которые запустили тесты."""
    if sys.platform == "win32":
        no_window = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        with contextlib.suppress(OSError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=no_window
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def test_module(root: Path, module: str, cancelled: Callable[[], bool] | None = None) -> ModuleCheck:
    """Тесты одного модуля (``packages/<модуль>/tests``) отдельным процессом pytest. Отмена
    (``cancelled``) завершает идущие тесты и поднимает ``AgenError`` с кодом ``cancelled``."""
    tests = root / "packages" / module / "tests"
    if not tests.is_dir():
        return ModuleCheck(module=module, ok=True, seconds=0.0, output="тестов нет")
    t0 = time.monotonic()
    extra: dict[str, object] = {}
    if sys.platform == "win32":
        # у сервера под оболочкой нет консоли: без окна у pytest
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    else:
        extra["start_new_session"] = True  # отмена завершает всю группу процессов тестов
    # вывод читается как UTF-8, поэтому и тесты пишут в UTF-8 (в консоли Windows иначе — cp1251)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(tests), "-q", "-p", "no:cacheprovider"],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **extra,  # type: ignore[call-overload]
    )
    deadline = t0 + TEST_TIMEOUT
    while True:
        try:
            output, _ = proc.communicate(timeout=POLL)
            ok = proc.returncode == 0
            break
        except subprocess.TimeoutExpired:
            if cancelled is not None and cancelled():
                _kill_tree(proc)
                proc.communicate()
                raise _cancelled() from None
            if time.monotonic() > deadline:
                _kill_tree(proc)
                proc.communicate()
                ok, output = False, f"Тесты не уложились в {TEST_TIMEOUT // 60} мин"
                break
    return ModuleCheck(module=module, ok=ok, seconds=round(time.monotonic() - t0, 1), output=output[-OUTPUT_LIMIT:])


def check_modules(root: Path, modules: list[str], ctx: JobContext | None = None) -> list[ModuleCheck]:
    """Тесты модулей по очереди; первый провал не останавливает остальные (видно всё сразу).
    Отмена задания останавливает и идущие тесты: ``AgenError`` с кодом ``cancelled``."""
    cancelled = ctx.cancelled if ctx is not None else None
    out = []
    for i, m in enumerate(modules):
        if ctx is not None:
            if ctx.cancelled():
                raise _cancelled()
            ctx.progress(f"Тесты модуля {m}", i, len(modules), "modules")
        out.append(test_module(root, m, cancelled))
    return out
