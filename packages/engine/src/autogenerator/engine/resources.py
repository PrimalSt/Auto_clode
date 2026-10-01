"""Память и потоки для вычислений (ARCHITECTURE.md, раздел 6.6).

Бюджет — 60% доступной (а не установленной) памяти на момент старта. Из него DuckDB
получает половину (``memory_limit``), процесс с пользовательским кодом — весь бюджет: пока
он работает, исполнитель ждёт. Лимит памяти процесса с кодом в Windows задаётся через Job
Object, в Linux и macOS — через ``RLIMIT_AS``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUDGET_SHARE = 0.6
FALLBACK_AVAILABLE = 4 << 30


def available_memory() -> int:
    """Доступная память в байтах (с учётом лимита контейнера в Linux)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        st = MemoryStatus()
        st.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return int(st.ullAvailPhys)
        return FALLBACK_AVAILABLE
    avail = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) * 1024
                break
    except OSError:
        pass
    if avail is None:
        try:
            avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            avail = FALLBACK_AVAILABLE
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            text = Path(f).read_text().strip()
        except OSError:
            continue
        if text.isdigit() and int(text) < avail:
            avail = int(text)
    return int(avail)


@dataclass
class Limits:
    """Лимиты вычислений одного задания."""

    budget: int
    threads: int

    @classmethod
    def default(cls) -> Limits:
        return cls(budget=int(available_memory() * BUDGET_SHARE), threads=max(1, os.cpu_count() or 1))

    @property
    def duckdb_memory(self) -> int:
        return max(256 << 20, self.budget // 2)

    @property
    def code_memory(self) -> int:
        return max(512 << 20, self.budget)


def gb(n: int) -> str:
    return f"{n / (1 << 30):.1f}".replace(".", ",") + " ГБ"


# --- Процесс с ограничением памяти ------------------------------------------------------


def set_own_memory_limit(limit: int | None) -> None:
    """Ограничить память своего процесса (Linux и macOS). В Windows лимит ставит родитель."""
    if not limit or sys.platform == "win32":
        return
    import resource

    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


class _WindowsJob:
    """Job Object с лимитом памяти процесса; при закрытии процесс завершается."""

    def __init__(self, limit: int):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint64)
                for n in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32: Any = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32
        self._handle = k32.CreateJobObjectW(None, None)
        info = Extended()
        process_memory, kill_on_close = 0x100, 0x2000
        info.BasicLimitInformation.LimitFlags = process_memory | kill_on_close
        info.ProcessMemoryLimit = limit
        k32.SetInformationJobObject(self._handle, 9, ctypes.byref(info), ctypes.sizeof(info))

    def assign(self, proc: subprocess.Popen[Any]) -> None:
        handle = getattr(proc, "_handle", None)
        if self._handle and handle is not None:
            self._k32.AssignProcessToJobObject(self._handle, int(handle))

    def close(self) -> None:
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


def run_limited(
    args: list[str], *, timeout: float | None, memory: int | None, env: dict[str, str] | None = None
) -> tuple[int | None, str]:
    """Запустить процесс с таймаутом и лимитом памяти. Возвращает код выхода (``None`` —
    завершён по таймауту) и его stderr."""
    job = None
    if memory and sys.platform == "win32":
        try:
            job = _WindowsJob(memory)
        except OSError:
            job = None
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=flags,
    )
    try:
        if job is not None:
            job.assign(proc)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
            return None, err.decode("utf-8", "replace")
        return proc.returncode, err.decode("utf-8", "replace")
    finally:
        if proc.poll() is None:
            proc.kill()
        if job is not None:
            job.close()
