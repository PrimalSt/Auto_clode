"""Кэш узлов: готовые результаты обработки входов, выборки для превью, узлы с кодом
(ARCHITECTURE.md, раздел 6.4, «Кэш узлов»).

Запись — Parquet и рядом JSON с подробностями (число строк, шаги). Ключ — хэш всего, от чего
зависит результат: описания узла и шагов, версий приложения и плагинов, отпечатка истории,
границ чтения, отчётного периода. Файлы пишутся атомарно (во временный файл, затем
переименование); ошибка чтения считается промахом, запись удаляется. Размер ограничен:
по умолчанию 20 ГБ или 10% свободного места, вытесняются давно не использованные записи.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import polars as pl

DEFAULT_MAX_BYTES = 20 << 30


def _version(dist: str) -> str:
    try:
        return version(dist)
    except PackageNotFoundError:
        return "?"


ENGINE_VERSION = "|".join(
    f"{d}={_version(d)}" for d in ("autogenerator-engine", "polars", "duckdb", "sqlglot", "pandas", "pyarrow")
)
"""Версии, от которых зависит результат любого узла: при обновлении кэш не подходит."""


def make_key(*parts: Any) -> str:
    """Ключ кэша из частей, которые сериализуются в JSON."""
    text = json.dumps([ENGINE_VERSION, *parts], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_max_bytes(folder: Path) -> int:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return DEFAULT_MAX_BYTES
    return min(DEFAULT_MAX_BYTES, max(1 << 30, free // 10))


class NodeCache:
    """Кэш узлов в папке ``folder``."""

    def __init__(self, folder: Path, max_bytes: int | None = None):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes if max_bytes is not None else default_max_bytes(folder)

    def _paths(self, key: str) -> tuple[Path, Path]:
        d = self.folder / key[:2]
        return d / f"{key}.parquet", d / f"{key}.json"

    def get(self, key: str) -> tuple[Path, dict[str, Any]] | None:
        """Файл и подробности записи; ``None`` — промах (в том числе при ошибке чтения)."""
        data, meta = self._paths(key)
        if not data.exists() or not meta.exists():
            return None
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            pl.scan_parquet(data).collect_schema()
        except Exception:
            self.drop(key)
            return None
        now = time.time()
        with contextlib.suppress(OSError):
            os.utime(meta, (now, now))
        return data, info

    def get_meta(self, key: str) -> dict[str, Any] | None:
        """Запись без таблицы (например, число строк истории)."""
        _, meta = self._paths(key)
        try:
            info: dict[str, Any] = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return info

    def put_meta(self, key: str, info: dict[str, Any]) -> None:
        _, meta = self._paths(key)
        meta.parent.mkdir(parents=True, exist_ok=True)
        tmp = meta.with_name(f"{meta.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, meta)

    def put(self, key: str, lf: pl.LazyFrame, info: dict[str, Any] | None = None) -> Path:
        """Записать таблицу в кэш (потоково) и вернуть путь к ней."""
        data, _ = self._paths(key)
        data.parent.mkdir(parents=True, exist_ok=True)
        tmp = data.with_name(f"{data.name}.{os.getpid()}.tmp")
        try:
            lf.sink_parquet(tmp, compression="zstd")
            os.replace(tmp, data)
        finally:
            tmp.unlink(missing_ok=True)
        self.put_meta(key, info or {})
        self.evict()
        return data

    def put_file(self, key: str, src: Path, info: dict[str, Any] | None = None) -> Path:
        """Перенести готовый Parquet в кэш."""
        data, _ = self._paths(key)
        data.parent.mkdir(parents=True, exist_ok=True)
        tmp = data.with_name(f"{data.name}.{os.getpid()}.tmp")
        shutil.copyfile(src, tmp)
        os.replace(tmp, data)
        self.put_meta(key, info or {})
        self.evict()
        return data

    def drop(self, key: str) -> None:
        for p in self._paths(key):
            with contextlib.suppress(OSError):
                p.unlink()

    def size(self) -> int:
        return sum(f.stat().st_size for f in self.folder.rglob("*") if f.is_file())

    def evict(self) -> None:
        """Удалить давно не использованные записи, пока кэш больше лимита."""
        entries: list[tuple[float, int, str]] = []
        total = 0
        for meta in self.folder.glob("*/*.json"):
            key = meta.stem
            data = meta.with_suffix(".parquet")
            try:
                size = meta.stat().st_size + (data.stat().st_size if data.exists() else 0)
                used = meta.stat().st_mtime
            except OSError:
                continue
            total += size
            entries.append((used, size, key))
        if total <= self.max_bytes:
            return
        for _, size, key in sorted(entries):
            self.drop(key)
            total -= size
            if total <= self.max_bytes:
                break

    def clear(self) -> int:
        """Очистить кэш; возвращает число освобождённых байт."""
        freed = self.size()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        return freed
