"""Папка данных: где она, что в ней лежит, кто ею владеет (ARCHITECTURE.md, разделы 2 и 9).

По умолчанию — ``%LOCALAPPDATA%\\Autogenerator`` в Windows и ``~/.local/share/autogenerator``
в Linux и macOS; другую папку задают переменной ``AGEN_HOME`` или параметром ``--home``.
Папка должна быть на локальном диске: SQLite в режиме WAL не работает в сетевых папках.
Метаданные пишет один процесс: он берёт блокировку папки (файл ``server.lock``).
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import IO

from autogenerator.contracts import DEFAULT_WORKSPACE, AgenError, ErrorCode

APP_DIR = "Autogenerator"
ENV_HOME = "AGEN_HOME"
LOCK_NAME = "server.lock"
SUBDIRS = ("db", "cache", "tmp", "backups", "logs")
SYNCED_MARKERS = ("onedrive", "dropbox", "sharepoint", "google drive", "яндекс.диск", "yandex.disk")


def default_home() -> Path:
    """Папка данных по умолчанию: переменная ``AGEN_HOME`` или стандартное место системы."""
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_DIR.lower()


def _is_network(path: Path) -> bool:
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    if sys.platform != "win32":
        return False
    import ctypes

    drive = os.path.splitdrive(str(path.resolve()))[0]
    if not drive:
        return False
    drive_remote = 4
    return bool(ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == drive_remote)


def check_location(path: Path) -> list[str]:
    """Проверить место для папки данных. Сетевая папка — ошибка, папка, которую
    синхронизирует облако, — предупреждение (синхронизация мешает базе и тяжёлым файлам)."""
    if _is_network(path):
        raise AgenError(
            ErrorCode.DATA_FOLDER,
            f"Папка данных {path} — в сети. Нужна папка на локальном диске",
            hint="SQLite в сетевой папке может повредить базу. Выберите, например, %LOCALAPPDATA%\\Autogenerator.",
        )
    low = str(path).lower()
    synced = [os.environ.get(k, "") for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")]
    if any(m in low for m in SYNCED_MARKERS) or any(s and low.startswith(s.lower()) for s in synced):
        return [
            f"Папка данных {path} синхронизируется облаком. Синхронизация может мешать базе и "
            "тяжёлым файлам истории; лучше выбрать папку вне OneDrive, Dropbox и SharePoint."
        ]
    return []


class FolderLock:
    """Исключительная блокировка папки данных на время записи метаданных.

    Блокировку держит открытый файл, поэтому после падения процесса она снимается сама.
    В файл записывается номер процесса и кто держит блокировку — для сообщения второму.
    """

    def __init__(self, path: Path, owner: str = "cli"):
        self.path = path
        self.owner = owner
        self._fh: IO[bytes] | None = None

    def acquire(self) -> FolderLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 — файл держит блокировку до release()
        try:
            _lock(fh)
        except OSError:
            fh.close()
            who = _holder(self.path)
            raise AgenError(
                ErrorCode.DATA_FOLDER_LOCKED,
                f"Папка данных {self.path.parent} занята: {who}",
                hint="Дождитесь окончания другой команды agen или закройте приложение.",
            ) from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"{self.owner}, процесс {os.getpid()}".encode())
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        fh = self._fh
        if fh is None:
            return
        self._fh = None
        try:
            _unlock(fh)
        finally:
            fh.close()

    def __enter__(self) -> FolderLock:
        return self.acquire()

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


def _holder(path: Path) -> str:
    """Кто держит блокировку — по тексту в файле блокировки.

    Читается без буфера и только начало файла: буферизованное чтение берёт 8 КБ и в Windows
    задевает запертый байт, тогда чтение падает с ошибкой блокировки.
    """
    try:
        with open(path, "rb", buffering=0) as raw:
            text = raw.read(512)
    except OSError:
        text = b""
    return text.decode("utf-8", "replace").strip() or "другой процесс"


if sys.platform == "win32":
    import msvcrt

    # Запирается байт за концом текста: тогда второй процесс может прочитать, кто держит папку.
    _LOCK_OFFSET = 1 << 20

    def _lock(fh: IO[bytes]) -> None:
        fh.seek(_LOCK_OFFSET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh: IO[bytes]) -> None:
        fh.seek(_LOCK_OFFSET)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@dataclass
class DataFolder:
    """Раскладка папки данных (раздел 9)."""

    root: Path
    workspace: str = DEFAULT_WORKSPACE
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def open(cls, root: str | Path | None = None, create: bool = True) -> DataFolder:
        """Открыть папку данных; ``create`` — создать, если её нет."""
        path = Path(root).expanduser() if root else default_home()
        warnings = check_location(path)
        if not path.exists():
            if not create:
                raise AgenError(ErrorCode.DATA_FOLDER, f"Папки данных {path} нет")
            path.mkdir(parents=True)
        elif not path.is_dir():
            raise AgenError(ErrorCode.DATA_FOLDER, f"{path} — файл, а не папка")
        folder = cls(path.resolve(), warnings=warnings)
        for d in SUBDIRS:
            (folder.root / d).mkdir(exist_ok=True)
        return folder

    @property
    def db_path(self) -> Path:
        return self.root / "db" / "autogenerator.sqlite"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def source_dir(self, source_id: str) -> Path:
        return self.root / self.workspace / "sources" / source_id

    def upload_dir(self, source_id: str, upload_id: str) -> Path:
        return self.source_dir(source_id) / "uploads" / upload_id

    def lock(self, owner: str = "cli") -> FolderLock:
        return FolderLock(self.root / LOCK_NAME, owner)

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def clean_tmp(self) -> None:
        """Удалить временные файлы прошлых запусков (при старте, под блокировкой)."""
        for p in self.tmp.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
