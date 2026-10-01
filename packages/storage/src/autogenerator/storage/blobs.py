"""Файлы загрузок в папке данных (``BlobStore``). URI — путь на локальном диске; на сервере
(позже) реализация заменится на S3, а модули обработки этого не заметят."""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path

from .folder import DataFolder


def _retry(fn: object, attempts: int = 10) -> None:
    """Повторить операцию: антивирус может ненадолго держать только что записанный файл."""
    assert callable(fn)
    for i in range(attempts):
        try:
            fn()
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.2 * (i + 1))


class LocalBlobStore:
    """``BlobStore`` на локальном диске."""

    def __init__(self, folder: DataFolder):
        self.folder = folder

    def upload_uri(self, source_id: str, upload_id: str) -> str:
        return self.folder.upload_dir(source_id, upload_id).as_posix()

    def tmp_uri(self) -> str:
        p = self.folder.tmp / uuid.uuid4().hex[:12]
        p.mkdir(parents=True)
        return p.as_posix()

    def put_file(self, src: str, uri: str) -> None:
        dst = Path(uri)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".partial")
        shutil.copyfile(src, tmp)
        _retry(lambda: os.replace(tmp, dst))

    def size(self, uri: str) -> int:
        p = Path(uri)
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0

    def delete(self, uri: str) -> None:
        p = Path(uri)
        if p.is_dir():
            _retry(lambda: shutil.rmtree(p))
        elif p.exists():
            _retry(p.unlink)

    def free_bytes(self) -> int:
        return self.folder.free_bytes()
