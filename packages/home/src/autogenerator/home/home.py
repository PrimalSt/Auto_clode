"""Папка данных приложения: источники, загрузки, история (F-151…F-163), сценарии и шаблоны
(``library.py``), запуски и резервные копии (``runs.py``), черновики сценария (``drafts.py``).

Метаданные пишет один процесс: ``write=True`` берёт блокировку папки данных до ``close``.
Тяжёлую работу (чтение файла, запись Parquet, профиль, сборку отчёта) делает исполнитель
``worker`` (``workers.WorkerApi``); здесь — только проверки до него и запись метаданных после.
В своём коде и CLI исполнитель — модуль ``autogenerator.worker`` в том же процессе
(``autogenerator.api.Home``), в сервере приложения — процессы-исполнители.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from autogenerator.contracts import (
    AgenError,
    ColumnProfile,
    CoverageReport,
    ErrorCode,
    HistoryManifest,
    IngestRequest,
    IngestResult,
    Issue,
    IssueLevel,
    OverlapPolicy,
    Period,
    PeriodFrom,
    PeriodUnit,
    ProgressCallback,
    ReadOptions,
    ReconcileResult,
    SchemaSnapshot,
    SourceRecord,
    SourceSpec,
    SourceVersionRecord,
    UploadRecord,
    UploadStatus,
)
from autogenerator.contracts.yaml_io import load_model_list
from autogenerator.storage import DataFolder, FolderLock, SqliteMetadataStore

from .base import HomeBase, file_sha256
from .drafts import DraftsMixin
from .library import LibraryMixin
from .runs import RunsMixin
from .workers import WorkerApi

DISK_FACTOR = 3
"""Перед загрузкой нужно свободного места примерно 3 × размер файла (раздел 6.1)."""

ChoosePolicy = Callable[[IngestResult], OverlapPolicy | None]


@dataclass
class MappingChoice:
    """Решение пользователя на экране сопоставления: подтверждённые пары «название в файле →
    id столбца» (запоминаются в aliases источника, F-605) и столбцы, которые оставить пустыми."""

    pairs: dict[str, str] = field(default_factory=dict)
    declined: list[str] = field(default_factory=list)


ChooseMapping = Callable[[SourceSpec, dict[str, ReconcileResult]], MappingChoice | None]
"""Спросить сопоставление: источник и сверка по файлам → решение или ``None`` (отмена)."""


@dataclass
class ColumnUsage:
    """Какие столбцы источника нужны сценариям из папки данных (F-603, F-606)."""

    required: list[str] | None
    """id нужных столбцов; ``None`` — неизвестно (источник не используют сохранённые сценарии)."""
    dependents: dict[str, list[str]] = field(default_factory=dict)
    """id столбца → кто его использует: «сценарий «…», вход sales: шаги dedupe, наборы by_region»."""


@dataclass
class UploadOutcome:
    """Итог загрузки: запись в истории и подробности задания."""

    record: UploadRecord
    result: IngestResult
    issues: list[Issue] = field(default_factory=list)
    remembered: dict[str, str] = field(default_factory=dict)
    """Подтверждённые в этой загрузке названия «в файле → id», запомненные в источнике."""


def _mapping_choice(e: AgenError, spec: SourceSpec, accept: bool, choose: ChooseMapping | None) -> MappingChoice | None:
    """Решение по сопоставлению, если загрузка остановлена сверкой структуры."""
    if e.code not in (ErrorCode.SCHEMA_REVIEW, ErrorCode.SCHEMA_BLOCKED):
        return None
    files = {k: ReconcileResult.model_validate(v) for k, v in (e.details or {}).get("files", {}).items()}
    if not files:
        return None
    if e.code == ErrorCode.SCHEMA_BLOCKED:
        # остановлена без предложений: выбрать можно, только если для нужных столбцов есть кандидаты
        if choose is None or not any(c in r.candidates for r in files.values() for c in r.missing_required):
            return None
        return choose(spec, files)
    if accept:
        pairs = {f: c for r in files.values() for f, c in r.proposed.items()}
        if pairs or choose is None:
            return MappingChoice(pairs)
    return choose(spec, files) if choose is not None else None


class Home(LibraryMixin, RunsMixin, DraftsMixin, HomeBase):
    """Папка данных: метаданные, файлы загрузок и блокировка записи."""

    @classmethod
    def open(
        cls,
        root: str | Path | None = None,
        write: bool = False,
        create: bool = True,
        *,
        worker: WorkerApi,
        owner: str = "cli",
    ) -> Self:
        """Открыть папку данных. ``write`` — взять блокировку записи (иначе — только чтение;
        если база ещё не создана или устарела, блокировка берётся на время миграции).
        Запуски, оставшиеся «идут» после сбоя, при открытии на запись помечаются прерванными.
        ``worker`` — кто выполняет тяжёлую работу (``workers.WorkerApi``), ``owner`` — кто держит
        папку (это увидит второй процесс в сообщении о занятой папке)."""
        folder = DataFolder.open(root, create=create)
        lock: FolderLock | None = folder.lock(owner)
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
            store.interrupt_running()
        return cls(folder, store, lock, worker)

    def __enter__(self) -> Self:
        return self

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
        period_from: PeriodFrom | None = None,
    ) -> tuple[SourceSpec, SchemaSnapshot]:
        """Черновик источника по выгрузке (не сохраняется). ``period_from=upload`` —
        выгрузка-срез: период задаётся при загрузке; пусто — определить по файлу."""
        return self.worker.draft_source(
            path, source_id, name, period_column, period_type, options, fmt, period_from=period_from
        )

    def create_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord:
        self._need_write()
        return self.store.create_source(spec, comment)

    def update_source(self, spec: SourceSpec, comment: str = "", force: bool = False) -> SourceRecord:
        """Новая версия настроек. Загрузки не переписываются: смена типа столбца
        применяется при чтении истории. Убрать столбец, который есть в загрузках, можно
        только с ``force``: загрузки хранят столбцы по id, и его данные пропадут из истории."""
        self._need_write()
        if not force:
            self._check_dropped(spec)
        return self.store.update_source(spec, comment)

    def _check_dropped(self, spec: SourceSpec) -> None:
        used = {cid for u in self.store.list_uploads(spec.id) for cid in u.mapping.values()}
        dropped = sorted(used - {c.id for c in spec.columns})
        if dropped:
            raise AgenError(
                ErrorCode.SOURCE_CHANGE,
                f"В загрузках источника «{spec.id}» есть столбцы, которых нет в новых настройках: "
                + ", ".join(dropped),
                hint="id столбцов после загрузок не меняют: новое название файла добавьте в aliases. "
                "Убрать столбец из истории можно с --force.",
            )

    def import_sources(self, path: str | Path, comment: str = "", force: bool = False) -> list[SourceRecord]:
        """Источники из YAML: новые создаются, существующие получают новую версию."""
        self._need_write()
        out = []
        for spec in load_model_list(SourceSpec, path):
            if self.store.has_source(spec.id):
                out.append(self.update_source(spec, comment or f"из {Path(path).name}", force))
            else:
                out.append(self.store.create_source(spec, comment or f"из {Path(path).name}"))
        return out

    def delete_source(self, source_id: str) -> None:
        """Удалить источник вместе со всеми загрузками и их файлами. Источник, который
        используют сохранённые сценарии, не удаляется."""
        self._need_write()
        uploads = self.store.list_uploads(source_id)
        self.store.delete_source(source_id)
        for u in uploads:
            self.blobs.delete(u.data_uri)
        self.blobs.delete(self.folder.source_dir(source_id).as_posix())

    # --- загрузки ----------------------------------------------------------------

    def uploads(self, source_id: str) -> list[UploadRecord]:
        return self.store.list_uploads(source_id)

    def upload_record(self, upload_id: str) -> UploadRecord:
        return self.store.get_upload(upload_id)

    def upload(
        self,
        source_id: str,
        path: str | Path | Sequence[str | Path],
        *,
        options: ReadOptions | None = None,
        period: Period | None = None,
        overlap_policy: OverlapPolicy | None = None,
        choose_policy: ChoosePolicy | None = None,
        accept_cast_errors: bool = False,
        mapping: Mapping[str, str] | None = None,
        declined: Sequence[str] = (),
        accept_mapping: bool = False,
        choose_mapping: ChooseMapping | None = None,
        required: Sequence[str] | None = None,
        force: bool = False,
        profile: bool = True,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> UploadOutcome:
        """Загрузить файл в историю источника.

        ``path`` — файл или список файлов одной выгрузки (выгрузка из нескольких частей):
        они склеиваются в одну загрузку по порядку. Тот же файл или тот же набор файлов
        (по SHA-256) второй раз не загружается без ``force``. Если у источника
        правило «ask» и период пересекается с прежними загрузками, правило берётся из
        ``overlap_policy`` или спрашивается через ``choose_policy``. Загрузка с ошибками
        приведения записывается со статусом «на проверке» и в историю не входит, пока её
        не примут (``accept_cast_errors`` или ``accept_upload``).

        Сопоставление (F-602…F-606): нужные столбцы — те, что используют сохранённые сценарии
        (``required`` задаёт их явно). Если столбец пропал, а в файле есть похожий, загрузка
        ждёт решения: ``mapping`` — подтверждённые пары «название в файле → id», ``declined`` —
        id, которые оставить пустыми, ``accept_mapping`` — принять предложенные пары,
        ``choose_mapping`` — спросить пользователя. Подтверждённые названия запоминаются в
        aliases источника (новая версия настроек), и в следующий раз сопоставятся сами.
        """
        self._need_write()
        src = self.store.get_source(source_id)
        spec = src.spec
        files = [Path(path)] if isinstance(path, str | Path) else [Path(x) for x in path]
        if not files:
            raise AgenError(ErrorCode.FILE_NOT_FOUND, "Не указан файл выгрузки")
        for f in files:
            if not f.is_file():
                raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Файл не найден: {f}")
        p = files[0]
        label = " + ".join(f.name for f in files)
        size = sum(f.stat().st_size for f in files)
        free = self.folder.free_bytes()
        if free < DISK_FACTOR * size:
            raise AgenError(
                ErrorCode.DISK_SPACE,
                f"Мало места на диске папки данных: свободно {free / 2**30:.1f} ГБ, "
                f"для загрузки {label} нужно около {DISK_FACTOR * size / 2**30:.1f} ГБ",
            )
        if overlap_policy == OverlapPolicy.ASK:
            raise AgenError(ErrorCode.SPEC_INVALID, "Для загрузки выберите конкретное правило, а не «ask»")
        if overlap_policy == OverlapPolicy.MERGE_DEDUPE and not spec.keys:
            raise AgenError(ErrorCode.SPEC_INVALID, f"Для merge_dedupe у источника «{spec.id}» нужны ключи (keys)")
        shas = [file_sha256(f, progress) for f in files]
        sha = shas[0] if len(shas) == 1 else hashlib.sha256("\n".join(shas).encode()).hexdigest()
        same = [u for u in self.store.find_uploads(sha) if u.source_id == spec.id]
        if same and not force:
            u = same[-1]
            what = "Этот файл уже загружен" if len(files) == 1 else "Эти файлы уже загружены"
            raise AgenError(
                ErrorCode.ALREADY_EXISTS,
                f"{what}: #{u.seq} {u.original_name} ({u.period.key}, {u.uploaded_at.astimezone():%d.%m.%Y %H:%M})",
                hint="Загрузить ещё раз: --force.",
            )

        ids = {c.id for c in spec.columns}
        pairs = dict(mapping or {})
        wrong = sorted({cid for cid in [*pairs.values(), *declined] if cid not in ids})
        if wrong:
            raise AgenError(
                ErrorCode.SPEC_INVALID,
                f"В источнике «{spec.id}» нет столбцов: {', '.join(wrong)}",
                hint="В сопоставлении справа — id столбца источника: agen source show " + spec.id,
            )
        usage = self.column_usage(spec.id) if required is None else ColumnUsage(list(required))
        stats = self._value_stats(spec.id)
        left_empty = set(declined)

        seq = self.store.next_upload_seq(spec.id)
        upload_id = f"{spec.id}-{seq:03d}-{uuid.uuid4().hex[:6]}"
        out_dir = self.blobs.upload_uri(spec.id, upload_id)
        while True:
            req = IngestRequest(
                source=self.worker.with_aliases(spec, pairs) if pairs else spec,
                path=str(p.resolve()),
                parts=[str(f.resolve()) for f in files[1:]],
                upload_id=upload_id,
                upload_seq=seq,
                out_dir=out_dir,
                options=options,
                required=usage.required,
                value_stats=stats,
                dependents=usage.dependents,
                declined=sorted(left_empty),
                period=period,
                history=self.store.history_manifest(spec.id),
                overlap_policy=overlap_policy,
                profile=profile,
            )
            try:
                res = self.worker.ingest_upload(req, progress=progress, cancelled=cancelled)
                break
            except AgenError as e:
                choice = _mapping_choice(e, spec, accept_mapping, choose_mapping)
                new_pairs = {f: c for f, c in (choice.pairs if choice else {}).items() if pairs.get(f) != c}
                new_declined = set(choice.declined if choice else ()) - left_empty
                if not new_pairs and not new_declined:
                    raise
                pairs.update(new_pairs)
                left_empty |= new_declined
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
                if chosen == OverlapPolicy.APPEND:
                    res.issues.append(
                        Issue(
                            level=IssueLevel.WARNING,
                            node=f"source:{spec.id}",
                            message=f"{label}: период {res.period.key} уже загружен; строки добавятся к прежним — "
                            "проверьте, нет ли двойного учёта",
                        )
                    )
            # подтверждённые названия запоминаются, только когда загрузка точно будет записана
            src, mapping_issues, remembered = self._remember_mapping(src, pairs, res)
            res.issues += mapping_issues
            up = res.upload
            status = up.status
            if status == UploadStatus.NEEDS_REVIEW and accept_cast_errors:
                status = UploadStatus.ACTIVE
            record = UploadRecord(
                id=upload_id,
                source_id=spec.id,
                seq=seq,
                source_version=src.version,
                original_name=label,
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
        return UploadOutcome(record=record, result=res, issues=res.issues, remembered=remembered)

    def column_usage(self, source_id: str) -> ColumnUsage:
        """Столбцы источника, которые явно используют сохранённые сценарии (шаги, наборы,
        показатели), и кто именно. Сценарий, который не удалось разобрать, делает
        использование неизвестным: тогда при загрузке важен каждый столбец."""
        refs = self.store.scenarios_using_source(source_id)
        if not refs:
            return ColumnUsage(None)
        required: set[str] = set()
        dependents: dict[str, list[str]] = {}
        known = True
        for sid in dict.fromkeys(r.scenario_id for r in refs if r.scenario_id):
            sc = self.store.get_scenario(sid)
            srcs = [self.store.get_source(x).spec for x in {i.source for i in sc.spec.inputs} if self.has_source(x)]
            try:
                usage = self.worker.column_usage(sc.spec, srcs)
            except AgenError:
                known = False
                continue
            for inp in sc.spec.inputs:
                if inp.source != source_id:
                    continue
                for col, nodes in usage.get(inp.id, {}).items():
                    required.add(col)
                    dependents.setdefault(col, []).append(f"сценарий «{sc.name}», вход {inp.id}: {', '.join(nodes)}")
        return ColumnUsage(sorted(required) if known else None, dependents)

    def _value_stats(self, source_id: str) -> dict[str, ColumnProfile]:
        """Профиль столбцов последней действующей загрузки: по нему сравниваются значения
        кандидатов для пропавших столбцов."""
        active = [u for u in self.store.list_uploads(source_id) if u.status == UploadStatus.ACTIVE and u.profile]
        return dict(active[-1].profile) if active else {}

    def _remember_mapping(
        self, src: SourceRecord, pairs: Mapping[str, str], res: IngestResult
    ) -> tuple[SourceRecord, list[Issue], dict[str, str]]:
        """Подтверждённые названия — в aliases источника (новая версия настроек, F-605)."""
        if not pairs:
            return src, [], {}
        node = f"source:{src.id}"
        in_file = {self.worker.normalize_name(f): f for f in res.reconcile.mapping}
        applied: dict[str, str] = {}
        issues: list[Issue] = []
        for fname, cid in pairs.items():
            actual = in_file.get(self.worker.normalize_name(fname))
            if actual is not None and res.reconcile.mapping[actual] == cid:
                applied[actual] = cid
            else:
                issues.append(
                    Issue(
                        level=IssueLevel.WARNING,
                        node=node,
                        message=f"Сопоставление «{fname}» → {cid} не применено: такого столбца в файле нет",
                    )
                )
        spec = self.worker.with_aliases(src.spec, applied)
        if spec == src.spec:
            return src, issues, {}
        what = "; ".join(f"«{f}» → {c}" for f, c in applied.items())
        src = self.store.update_source(spec, f"сопоставление подтверждено: {what}")
        issues.append(
            Issue(
                level=IssueLevel.INFO,
                node=node,
                message=f"Запомнено сопоставление: {what} (настройки источника, версия {src.version})",
            )
        )
        return src, issues, applied

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
        outside = self.worker.rows_outside(u.data_uri, spec, period)
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
        return self.worker.coverage_report(self.history(source_id))

    def default_period(self, source_id: str) -> Period:
        """Отчётный период по умолчанию: загрузка с самым поздним концом периода."""
        return self.worker.default_period(self.history(source_id))

    def disk_usage(self, source_id: str) -> int:
        return self.blobs.size(self.folder.source_dir(source_id).as_posix())

    def export_history(self, source_id: str, out: str | Path, columns: list[str] | None = None) -> int:
        """Действующая история источника в один Parquet; возвращает число строк."""
        return self.worker.export_history(self.history(source_id), out, columns)
