"""Папка данных приложения из своего кода и CLI: источники, загрузки, история (F-151…F-163).

Пример::

    from autogenerator.api import Home
    with Home.open(write=True) as home:
        spec, snap = home.draft_source("Продажи_2026-01.csv", "sales")
        home.create_source(spec)
        out = home.upload("sales", "Продажи_2026-01.csv")
        print(out.record.period.key, out.record.rows)
        print(home.coverage("sales").gaps)

Метаданные пишет один процесс: ``write=True`` берёт блокировку папки данных до ``close``.
Тяжёлую работу (чтение файла, запись Parquet, профиль) делает задание исполнителя
``worker.ingest_upload``; здесь — только проверки до него и запись метаданных после.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from autogenerator import worker
from autogenerator.contracts import (
    AgenError,
    CoverageReport,
    ErrorCode,
    HistoryManifest,
    IngestRequest,
    IngestResult,
    Issue,
    OverlapPolicy,
    Period,
    PeriodUnit,
    ProgressCallback,
    ReadOptions,
    ReadProgress,
    SchemaSnapshot,
    SourceRecord,
    SourceSpec,
    SourceVersionRecord,
    UploadRecord,
    UploadStatus,
)
from autogenerator.contracts.yaml_io import load_model_list
from autogenerator.storage import DataFolder, FolderLock, LocalBlobStore, SqliteMetadataStore

DISK_FACTOR = 3
"""Перед загрузкой нужно свободного места примерно 3 × размер файла (раздел 6.1)."""

ChoosePolicy = Callable[[IngestResult], OverlapPolicy | None]


@dataclass
class UploadOutcome:
    """Итог загрузки: запись в истории и подробности задания."""

    record: UploadRecord
    result: IngestResult
    issues: list[Issue] = field(default_factory=list)


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


class Home:
    """Папка данных: метаданные, файлы загрузок и блокировка записи."""

    def __init__(self, folder: DataFolder, store: SqliteMetadataStore, lock: FolderLock | None):
        self.folder = folder
        self.store = store
        self.blobs = LocalBlobStore(folder)
        self._lock = lock

    @classmethod
    def open(cls, root: str | Path | None = None, write: bool = False, create: bool = True) -> Home:
        """Открыть папку данных. ``write`` — взять блокировку записи (иначе — только чтение;
        если база ещё не создана или устарела, блокировка берётся на время миграции)."""
        folder = DataFolder.open(root, create=create)
        lock: FolderLock | None = folder.lock()
        try:
            assert lock is not None
            lock.acquire()
        except AgenError:
            if write:
                raise
            lock = None
        try:
            store = SqliteMetadataStore(folder.db_path, folder.backups, folder.workspace)
        except BaseException:
            if lock is not None:
                lock.release()
            raise
        if lock is not None and not write:
            lock.release()
            lock = None
        elif lock is not None:
            folder.clean_tmp()
        return cls(folder, store, lock)

    @property
    def writable(self) -> bool:
        return self._lock is not None

    def close(self) -> None:
        self.store.close()
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def __enter__(self) -> Home:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def _need_write(self) -> None:
        if not self.writable:
            raise AgenError(ErrorCode.DATA_FOLDER_LOCKED, "Папка данных открыта только для чтения")

    # --- источники ---------------------------------------------------------------

    def sources(self) -> list[SourceRecord]:
        return self.store.list_sources()

    def source(self, source_id: str) -> SourceRecord:
        return self.store.get_source(source_id)

    def has_source(self, source_id: str) -> bool:
        return self.store.has_source(source_id)

    def source_versions(self, source_id: str) -> list[SourceVersionRecord]:
        return self.store.source_versions(source_id)

    def draft_source(
        self,
        path: str | Path,
        source_id: str,
        name: str | None = None,
        period_column: str | None = None,
        period_type: PeriodUnit | None = None,
        options: ReadOptions | None = None,
        fmt: str | None = None,
    ) -> tuple[SourceSpec, SchemaSnapshot]:
        """Черновик источника по выгрузке (не сохраняется)."""
        return worker.draft_source(path, source_id, name, period_column, period_type, options, fmt)

    def create_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord:
        self._need_write()
        return self.store.create_source(spec, comment)

    def update_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord:
        """Новая версия настроек. Загрузки не переписываются: смена типа столбца
        применяется при чтении истории."""
        self._need_write()
        return self.store.update_source(spec, comment)

    def import_sources(self, path: str | Path, comment: str = "") -> list[SourceRecord]:
        """Источники из YAML: новые создаются, существующие получают новую версию."""
        self._need_write()
        out = []
        for spec in load_model_list(SourceSpec, path):
            if self.store.has_source(spec.id):
                out.append(self.store.update_source(spec, comment or f"из {Path(path).name}"))
            else:
                out.append(self.store.create_source(spec, comment or f"из {Path(path).name}"))
        return out

    def delete_source(self, source_id: str) -> None:
        """Удалить источник вместе со всеми загрузками и их файлами."""
        self._need_write()
        for u in self.store.list_uploads(source_id):
            self.blobs.delete(u.data_uri)
        self.store.delete_source(source_id)
        self.blobs.delete(self.folder.source_dir(source_id).as_posix())

    # --- загрузки ----------------------------------------------------------------

    def uploads(self, source_id: str) -> list[UploadRecord]:
        return self.store.list_uploads(source_id)

    def upload_record(self, upload_id: str) -> UploadRecord:
        return self.store.get_upload(upload_id)

    def upload(
        self,
        source_id: str,
        path: str | Path,
        *,
        options: ReadOptions | None = None,
        period: Period | None = None,
        overlap_policy: OverlapPolicy | None = None,
        choose_policy: ChoosePolicy | None = None,
        accept_cast_errors: bool = False,
        required: Sequence[str] = (),
        force: bool = False,
        profile: bool = True,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> UploadOutcome:
        """Загрузить файл в историю источника.

        Тот же файл (по SHA-256) второй раз не загружается без ``force``. Если у источника
        правило «ask» и период пересекается с прежними загрузками, правило берётся из
        ``overlap_policy`` или спрашивается через ``choose_policy``. Загрузка с ошибками
        приведения записывается со статусом «на проверке» и в историю не входит, пока её
        не примут (``accept_cast_errors`` или ``accept_upload``).
        """
        self._need_write()
        src = self.store.get_source(source_id)
        spec = src.spec
        p = Path(path)
        if not p.is_file():
            raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {p}")
        size = p.stat().st_size
        free = self.folder.free_bytes()
        if free < DISK_FACTOR * size:
            raise AgenError(
                ErrorCode.DISK_SPACE,
                f"Мало места на диске папки данных: свободно {free / 2**30:.1f} ГБ, "
                f"для загрузки {p.name} нужно около {DISK_FACTOR * size / 2**30:.1f} ГБ",
            )
        if overlap_policy == OverlapPolicy.ASK:
            raise AgenError(ErrorCode.SPEC_INVALID, "Для загрузки выберите конкретное правило, а не «ask»")
        if overlap_policy == OverlapPolicy.MERGE_DEDUPE and not spec.keys:
            raise AgenError(ErrorCode.SPEC_INVALID, f"Для merge_dedupe у источника «{spec.id}» нужны ключи (keys)")
        sha = file_sha256(p, progress)
        same = [u for u in self.store.find_uploads(sha) if u.source_id == spec.id]
        if same and not force:
            u = same[-1]
            raise AgenError(
                ErrorCode.ALREADY_EXISTS,
                f"Этот файл уже загружен: #{u.seq} {u.original_name} ({u.period.key}, "
                f"{u.uploaded_at.astimezone():%d.%m.%Y %H:%M})",
                hint="Загрузить ещё раз: --force.",
            )

        seq = self.store.next_upload_seq(spec.id)
        upload_id = f"{spec.id}-{seq:03d}-{uuid.uuid4().hex[:6]}"
        out_dir = self.blobs.upload_uri(spec.id, upload_id)
        req = IngestRequest(
            source=spec,
            path=str(p.resolve()),
            upload_id=upload_id,
            upload_seq=seq,
            out_dir=out_dir,
            options=options,
            required=list(required),
            period=period,
            history=self.store.history_manifest(spec.id),
            overlap_policy=overlap_policy,
            profile=profile,
        )
        res = worker.ingest_upload(req, progress=progress, cancelled=cancelled)
        try:
            chosen: OverlapPolicy | None = overlap_policy
            if res.needs_overlap_choice:
                chosen = choose_policy(res) if choose_policy is not None else None
                if chosen is None or chosen == OverlapPolicy.ASK:
                    raise AgenError(
                        ErrorCode.OVERLAP_CHOICE,
                        f"Период {res.period.key} уже загружен ("
                        + ", ".join(self._label(u) for u in res.overlaps)
                        + "), а правило пересечения для этой загрузки не выбрано",
                        hint="Укажите --overlap replace_period (заменить период), append (добавить), "
                        "merge_dedupe (объединить по ключам) или replace_all (заменить всё).",
                    )
                if chosen == OverlapPolicy.MERGE_DEDUPE and not spec.keys:
                    raise AgenError(
                        ErrorCode.SPEC_INVALID, f"Для merge_dedupe у источника «{spec.id}» нужны ключи (keys)"
                    )
            up = res.upload
            status = up.status
            if status == UploadStatus.NEEDS_REVIEW and accept_cast_errors:
                status = UploadStatus.ACTIVE
            record = UploadRecord(
                id=upload_id,
                source_id=spec.id,
                seq=seq,
                source_version=src.version,
                original_name=p.name,
                sha256=sha,
                size=size,
                format=res.snapshot.format,
                options=up.options,
                raw_uri=None,
                data_uri=up.data_uri,
                rejects_uri=up.rejects_uri,
                schema_snapshot=res.snapshot,
                cast_report=up.cast_issues,
                mapping=res.reconcile.mapping,
                profile=res.profile,
                period=res.period,
                period_from_data=res.period_from_data,
                rows=up.rows,
                rows_outside_period=res.rows_outside_period,
                null_period_rows=up.null_period_rows,
                status=status,
                review_reasons=up.review_reasons,
                overlap_policy=chosen if spec.overlap_policy == OverlapPolicy.ASK else overlap_policy,
                data_bytes=up.bytes_written,
                uploaded_at=datetime.now(UTC).replace(microsecond=0),
            )
            self.store.add_upload(record)
        except BaseException:
            self.blobs.delete(out_dir)
            raise
        return UploadOutcome(record=record, result=res, issues=res.issues)

    def _label(self, upload_id: str) -> str:
        u = self.store.get_upload(upload_id)
        return f"#{u.seq} {u.period.key}"

    def set_upload_status(self, upload_id: str, status: UploadStatus) -> UploadRecord:
        """Исключить загрузку из истории, вернуть её или принять загрузку «на проверке»."""
        self._need_write()
        return self.store.update_upload(upload_id, status=status)

    def set_upload_period(self, upload_id: str, period: Period) -> UploadRecord:
        """Поправить объявленный период загрузки (для типа «range» — подтвердить)."""
        self._need_write()
        u = self.store.get_upload(upload_id)
        spec = self.store.get_source(u.source_id).spec
        outside = worker.rows_outside(u.data_uri, spec, period)
        return self.store.update_upload(upload_id, period=period, rows_outside_period=outside)

    def set_upload_policy(self, upload_id: str, policy: OverlapPolicy | None) -> UploadRecord:
        self._need_write()
        return self.store.update_upload(upload_id, overlap_policy=policy)

    def delete_upload(self, upload_id: str) -> None:
        """Удалить загрузку и её файлы."""
        self._need_write()
        u = self.store.get_upload(upload_id)
        self.store.delete_upload(upload_id)
        self.blobs.delete(u.data_uri)

    # --- история -----------------------------------------------------------------

    def history(self, source_id: str) -> HistoryManifest:
        return self.store.history_manifest(source_id)

    def coverage(self, source_id: str) -> CoverageReport:
        return worker.coverage_report(self.history(source_id))

    def default_period(self, source_id: str) -> Period:
        """Отчётный период по умолчанию: загрузка с самым поздним концом периода."""
        return worker.default_period(self.history(source_id))

    def disk_usage(self, source_id: str) -> int:
        return self.blobs.size(self.folder.source_dir(source_id).as_posix())

    def export_history(self, source_id: str, out: str | Path, columns: list[str] | None = None) -> int:
        """Действующая история источника в один Parquet; возвращает число строк."""
        return worker.export_history(self.history(source_id), out, columns)
