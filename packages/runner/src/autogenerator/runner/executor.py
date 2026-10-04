"""Исполнитель в отдельном процессе (``ExecutorBackend``, ARCHITECTURE.md, раздел 6.6).

Процесс-исполнитель импортирует модуль исполнителя (``autogenerator.worker``) по имени и
выполняет вызовы по одному. Сервер этот модуль не импортирует: ошибка в модуле обработки
или плагине ломает только вызов, а не сервер. Аргументы и итог передаются через pickle,
ход работы (``progress``) — сообщениями, отмена (``cancelled``) — общим флагом.

Если процесс упал, вызов получает ошибку ``worker_failed``, а следующий вызов запускает
процесс заново. Таймаут и отмена, которую исполнитель не заметил, завершают процесс.
После ``max_calls`` вызовов процесс перезапускается, чтобы память и настройки библиотек не
копились. Модуль исполнителя может попросить перезапуск после вызова — функцией
``restart_requested()`` (``autogenerator.worker``: в процессе выполнялся код пользователя); новый
процесс тогда запускается сразу, в фоне.

В Windows под оболочкой (переменная ``AGEN_WINDOWLESS``) исполнитель установленного приложения
запускается через pythonw.exe: так у него точно нет консольного окна. Сервер оболочка запускает
со скрытой консолью (``CREATE_NO_WINDOW``), её наследуют и дочерние python.exe; поэтому в
окружении ``.venv`` (режим разработчика) исполнитель остаётся python.exe — pythonw.exe там лишь
переадресует к настоящему Python, и завершение исполнителя не дошло бы до него.
"""

from __future__ import annotations

import contextlib
import importlib
import multiprocessing as mp
import os
import pickle
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Any

from autogenerator.contracts import AgenError, ErrorCode, ExecutorInfo, ProgressCallback

POLL = 0.1
"""Как часто ждущий вызов проверяет процесс, таймаут и отмену (с)."""


def _portable(exc: BaseException) -> BaseException:
    """Исключение, которое переживёт pickle; иначе — ``AgenError`` с его текстом."""
    try:
        pickle.loads(pickle.dumps(exc))
        return exc
    except Exception:
        return AgenError(ErrorCode.WORKER_FAILED, f"{type(exc).__name__}: {exc}")


def _restart_requested(module: Any) -> bool:
    check = getattr(module, "restart_requested", None)
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return True  # состояние процесса неизвестно: надёжнее начать заново


def windowless_executable() -> str | None:
    """pythonw.exe рядом с python.exe, если сервер работает под оболочкой Windows не из ``.venv``,
    иначе None."""
    if sys.platform != "win32" or not os.environ.get("AGEN_WINDOWLESS") or sys.prefix != sys.base_prefix:
        return None
    exe = Path(sys.executable).with_name("pythonw.exe")
    return str(exe) if exe.is_file() else None


def _child(target: str, conn: Connection, cancel: Event, env: Mapping[str, str]) -> None:
    """Цикл процесса-исполнителя."""
    os.environ.update(env)
    if sys.stdout is None or sys.stderr is None:
        # pythonw: потоков вывода нет, а модули и библиотеки иногда печатают
        sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 — живёт, пока жив процесс
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    try:
        module = importlib.import_module(target)
    except BaseException:
        conn.send(("fatal", traceback.format_exc()))
        return
    send_lock = threading.Lock()

    def send(msg: tuple[Any, ...]) -> None:
        with send_lock:
            conn.send(msg)

    send(("ready", os.getpid()))
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return  # сервер закрылся или пропал
        if msg[0] == "stop":
            return
        _, call_id, fn, args, kwargs, flags = msg
        if "progress" in flags:
            kwargs["progress"] = lambda p, _id=call_id: send(("progress", _id, p))
        if "cancelled" in flags:
            kwargs["cancelled"] = cancel.is_set
        try:
            value = getattr(module, fn)(*args, **kwargs)
        except BaseException as e:
            send(("error", call_id, _portable(e), traceback.format_exc(), _restart_requested(module)))
            continue
        out: tuple[Any, ...] = ("ok", call_id, value, _restart_requested(module))
        try:
            pickle.dumps(out)
        except Exception as e:
            err = AgenError(ErrorCode.WORKER_FAILED, f"Итог {fn} нельзя передать из исполнителя: {e}")
            out = ("error", call_id, err, traceback.format_exc(), out[3])
        send(out)


class ProcessExecutor:
    """Один процесс-исполнитель; вызовы выполняются по очереди."""

    def __init__(
        self,
        target: str = "autogenerator.worker",
        name: str = "main",
        *,
        timeout: float | None = None,
        max_calls: int | None = 200,
        cancel_grace: float = 5.0,
        start_timeout: float = 120.0,
        env: Mapping[str, str] | None = None,
    ):
        self.target = target
        self.name = name
        self.timeout = timeout
        self.max_calls = max_calls
        self.cancel_grace = cancel_grace
        self.start_timeout = start_timeout
        self.env = dict(env or {})
        self._ctx = mp.get_context("spawn")
        if exe := windowless_executable():
            self._ctx.set_executable(exe)
        self._lock = threading.Lock()
        self._proc: Any = None
        self._conn: Connection | None = None
        self._cancel: Event | None = None
        self._pid: int | None = None
        self._calls = 0
        self._restarts = 0
        self._started = 0
        self._error: str | None = None
        self._seq = 0
        self._restart_after = False

    # --- процесс -------------------------------------------------------------------

    def start(self) -> None:
        """Запустить процесс заранее (иначе — при первом вызове)."""
        with self._lock:
            self._ensure()

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        self._reset()
        parent, child = self._ctx.Pipe(duplex=True)
        cancel = self._ctx.Event()
        proc = self._ctx.Process(
            target=_child, args=(self.target, child, cancel, self.env), name=f"agen-{self.name}", daemon=True
        )
        proc.start()
        child.close()
        self._proc, self._conn, self._cancel = proc, parent, cancel
        if self._started:
            self._restarts += 1
        self._started += 1
        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            if parent.poll(POLL):
                try:
                    msg = parent.recv()
                except (EOFError, OSError):
                    break
                if msg[0] == "ready":
                    self._pid, self._error = msg[1], None
                    return
                if msg[0] == "fatal":
                    self._error = msg[1]
                    self._kill()
                    raise AgenError(
                        ErrorCode.WORKER_FAILED,
                        f"Исполнитель «{self.name}» не запустился: модуль {self.target} не загрузился",
                        details={"traceback": msg[1]},
                        hint="Подробности — на экране «Модули». Остальное приложение работает.",
                    )
            elif not proc.is_alive():
                break
        code = proc.exitcode
        self._error = f"процесс не ответил (код выхода {code})"
        self._kill()
        raise AgenError(ErrorCode.WORKER_FAILED, f"Исполнитель «{self.name}» не запустился: {self._error}")

    def _kill(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = self._cancel = None
        self._pid = None
        self._calls = 0
        if conn is not None:
            conn.close()
        if proc is not None:
            if proc.is_alive():
                proc.kill()
            proc.join(5)

    def _reset(self) -> None:
        if self._proc is not None:
            self._kill()

    def _stop(self) -> None:
        """Мягко остановить процесс: попросить выйти, подождать, иначе завершить."""
        proc, conn = self._proc, self._conn
        if proc is None or conn is None:
            return
        with contextlib.suppress(OSError, ValueError):
            conn.send(("stop",))
        proc.join(5)
        self._kill()

    def close(self) -> None:
        with self._lock:
            self._stop()

    def info(self) -> ExecutorInfo:
        alive = self._proc is not None and self._proc.is_alive()
        return ExecutorInfo(
            name=self.name,
            pid=self._pid if alive else None,
            alive=alive,
            calls=self._calls,
            restarts=self._restarts,
            error=self._error,
        )

    # --- вызов ---------------------------------------------------------------------

    def call(
        self,
        fn: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        cancelled: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Вызвать ``fn`` модуля исполнителя и дождаться итога. Исключение исполнителя
        поднимается здесь же (``AgenError`` — как есть).

        ``progress`` и ``cancelled`` среди ``kwargs`` передаются функции через границу процессов.
        ``cancelled`` вызова только останавливает его: функция узнаёт об отмене, если сама
        принимает ``cancelled``, а иначе через ``cancel_grace`` секунд процесс завершается."""
        limit = timeout if timeout is not None else self.timeout
        kw = dict(kwargs or {})
        progress: ProgressCallback | None = kw.pop("progress", None)
        own: Callable[[], bool] | None = kw.pop("cancelled", None)
        watch = [c for c in (own, cancelled) if c is not None]
        with self._lock:
            self._ensure()
            assert self._conn is not None and self._cancel is not None
            conn, cancel = self._conn, self._cancel
            cancel.clear()
            self._restart_after = False
            self._seq += 1
            call_id = self._seq
            flags = [k for k, v in (("progress", progress), ("cancelled", own)) if v is not None]
            conn.send(("call", call_id, fn, args, kw, flags))
            try:
                return self._wait(call_id, progress, lambda: any(c() for c in watch) if watch else False, limit)
            finally:
                if self._proc is not None:
                    self._calls += 1
                    if self._restart_after or (self.max_calls and self._calls >= self.max_calls):
                        self._stop()
                        if self._restart_after:
                            threading.Thread(target=self._prestart, name=f"agen-start-{self.name}", daemon=True).start()

    def _prestart(self) -> None:
        with contextlib.suppress(AgenError):  # ошибка запуска видна в info() и придёт следующему вызову
            self.start()

    def _wait(
        self,
        call_id: int,
        progress: ProgressCallback | None,
        cancelled: Callable[[], bool],
        limit: float | None,
    ) -> Any:
        assert self._conn is not None and self._cancel is not None and self._proc is not None
        conn, cancel, proc = self._conn, self._cancel, self._proc
        start = time.monotonic()
        cancel_at: float | None = None
        while True:
            eof = False
            try:
                ready = conn.poll(POLL)
                msg = conn.recv() if ready else None
            except (EOFError, OSError):
                eof, msg = True, None
            if msg is not None:
                if msg[0] == "progress":
                    if progress is not None and msg[1] == call_id:
                        progress(msg[2])
                    continue
                if msg[0] == "ok" and msg[1] == call_id:
                    self._restart_after = bool(msg[3])
                    return msg[2]
                if msg[0] == "error" and msg[1] == call_id:
                    self._restart_after = bool(msg[4])
                    exc: BaseException = msg[2]
                    if not isinstance(exc, AgenError):
                        exc.add_note(f"Исполнитель «{self.name}»:\n{msg[3]}")
                    raise exc
            if eof or not proc.is_alive():
                proc.join(1)
                code = proc.exitcode
                self._kill()
                raise AgenError(
                    ErrorCode.WORKER_FAILED,
                    f"Исполнитель «{self.name}» завершился аварийно (код выхода {code})",
                    hint="Его процесс запущен заново: задание можно повторить.",
                )
            now = time.monotonic()
            if limit is not None and now - start > limit:
                self._kill()
                raise AgenError(ErrorCode.TIMEOUT, f"Задание не уложилось в {limit:.0f} с и остановлено")
            if cancel_at is None and cancelled():
                cancel.set()
                cancel_at = now
            if cancel_at is not None and now - cancel_at > self.cancel_grace:
                self._kill()
                raise AgenError(ErrorCode.CANCELLED, "Задание отменено")
