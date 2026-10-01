"""Тесты ingest не зависят от встроенных читателей: вместо них — читатель JSON-строк.
Так ошибка в readers-std не роняет тесты этого модуля."""

import json
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pytest

from autogenerator.contracts import ProgressCallback, ReaderPlugin, ReadOptions, ReadProgress
from autogenerator.plugin_host import PluginRegistry


class JsonLinesReader(ReaderPlugin):
    """Файл .jsonl: первая строка — список названий столбцов, дальше — строки значений."""

    name = "jsonl"
    formats = ("jsonl",)

    def can_read(self, path: Path) -> bool:
        return path.suffix == ".jsonl"

    def sniff(self, path: Path, options: ReadOptions) -> ReadOptions:
        return options

    def batches(
        self,
        path: Path,
        options: ReadOptions,
        batch_rows: int = 100_000,
        progress: ProgressCallback | None = None,
    ) -> Iterator[pa.RecordBatch]:
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
        names, rows = lines[0], lines[1:]
        for i in range(0, len(rows), batch_rows):
            if progress is not None:
                progress(ReadProgress("чтение", i, len(rows), "lines"))
            chunk = rows[i : i + batch_rows]
            cols = [pa.array([r[j] for r in chunk], pa.string()) for j in range(len(names))]
            yield pa.RecordBatch.from_arrays(cols, names=names)


def write_jsonl(path: Path, names: list[str], rows: list[list[str | None]]) -> Path:
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [names, *rows]), encoding="utf-8")
    return path


@pytest.fixture
def jsonl():
    return write_jsonl


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry.from_plugins([JsonLinesReader])
