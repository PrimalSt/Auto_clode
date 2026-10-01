"""Хранилища для одного пользователя на компьютере: папка данных, метаданные в SQLite,
файлы загрузок на диске (ARCHITECTURE.md, разделы 4.2 и 9). Модули обработки этот пакет
не импортируют: они получают манифесты и пути от ``worker``."""

from .blobs import LocalBlobStore
from .folder import DataFolder, FolderLock, check_location, default_home
from .metadata import SqliteMetadataStore

__all__ = [
    "DataFolder",
    "FolderLock",
    "LocalBlobStore",
    "SqliteMetadataStore",
    "check_location",
    "default_home",
]
