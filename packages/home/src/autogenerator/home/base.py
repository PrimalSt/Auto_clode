"""Общее для частей ``Home``: папка данных, метаданные, файлы и блокировка записи."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import TracebackType

from autogenerator.contracts import AgenError, ErrorCode, ProgressCallback, ReadProgress
from autogenerator.storage import DataFolder, FolderLock, LocalBlobStore, SqliteMetadataStore

from .workers import WorkerApi


def file_sha256(path: Path, progress: ProgressCallback | None = None) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(ReadProgress("проверка файла", done, total, "bytes"))
    return h.hexdigest()


class HomeBase:
    """Папка данных: метаданные, файлы загрузок и блокировка записи. Тяжёлую работу делает
    исполнитель ``worker`` (``workers.WorkerApi``)."""

    def __init__(self, folder: DataFolder, store: SqliteMetadataStore, lock: FolderLock | None, worker: WorkerApi):
        self.folder = folder
        self.store = store
        self.blobs = LocalBlobStore(folder)
        self.worker = worker
        self._lock = lock

    @property
    def writable(self) -> bool:
        return self._lock is not None

    def close(self) -> None:
        self.store.close()
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def _need_write(self) -> None:
        if not self.writable:
            raise AgenError(ErrorCode.DATA_FOLDER_LOCKED, "Папка данных открыта только для чтения")
