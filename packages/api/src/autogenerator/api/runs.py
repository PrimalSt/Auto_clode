"""Запуски сценариев из папки данных и резервные копии базы (F-607…F-609a, F-804).

Запуск берёт версию сценария, версию его шаблона и текущую историю каждого входа, собирает
отчёт в папку данных (``local/outputs/{run_id}/``) и, если указано, копирует его в выбранную
папку. Журнал запуска — запись ``RunRecord``: версии сценария, источников, шаблона,
приложения и плагинов, состав истории, узлы и замечания (F-608).
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autogenerator import worker
from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    Issue,
    IssueLevel,
    Period,
    RunRecord,
    RunRequest,
    RunStatus,
    SourceRecord,
)
from autogenerator.storage import SqliteMetadataStore

from .base import HomeBase

BACKUP_GLOB = "autogenerator-*.sqlite"


@dataclass
class BackupInfo:
    path: Path
    size: int
    created: datetime


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _busy(path: Path) -> bool:
    """Файл открыт в другой программе (PowerPoint держит открытый .pptx на запись)."""
    if not path.exists():
        return False
    try:
        with path.open("ab"):
            return False
    except PermissionError:
        return True


def copy_report(src: Path, dst: Path, issues: list[Issue]) -> Path:
    """Копия отчёта в выбранную папку или файл. Если файл открыт в PowerPoint, копия ложится
    рядом как «… (2).pptx» с пометкой в журнале (F-607)."""
    if dst.is_dir() or (not dst.exists() and dst.suffix.lower() != ".pptx"):
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    target, n = dst, 2
    while _busy(target):
        target = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
        n += 1
        if n > 99:
            raise AgenError(ErrorCode.OUTPUT_BUSY, f"Файл {dst.name} и его копии заняты")
    if target != dst:
        issues.append(
            Issue(
                level=IssueLevel.WARNING, message=f"{dst.name} открыт в другой программе; сохранено как {target.name}"
            )
        )
    tmp = target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


class RunsMixin(HomeBase):
    # --- запуски -----------------------------------------------------------------

    def run_scenario(
        self,
        scenario_id: str,
        *,
        period: str | Period | None = None,
        version: int | None = None,
        output: str | Path | None = None,
        accept_cast_errors: bool = False,
        workdir: str | Path | None = None,
        trigger: str = "cli",
    ) -> RunRecord:
        """Собрать отчёт по сохранённому сценарию. ``period`` — любой отчётный период из
        истории или произвольный диапазон (F-609a): данные — из текущей истории, включая
        исправления, загруженные позже; по умолчанию — последний период основного входа.
        ``version`` — версия сценария (по умолчанию текущая). ``output`` — папка или файл,
        куда положить копию отчёта; сам отчёт остаётся в папке данных."""
        self._need_write()
        sc = self.store.get_scenario(scenario_id)
        v = sc.current if version is None else self.store.get_scenario_version(scenario_id, version)
        if v.theme_id is None or v.theme_version is None:
            raise AgenError(
                ErrorCode.SPEC_INVALID,
                f"У сценария «{scenario_id}» нет шаблона оформления",
                hint="Сохраните сценарий с шаблоном: agen scenario add сценарий.yaml --theme <id или файл .pptx>",
            )
        tv = self.store.get_theme_version(v.theme_id, v.theme_version)
        sources: dict[str, SourceRecord] = {}
        for inp in v.spec.inputs:
            if inp.source not in sources:
                sources[inp.source] = self.store.get_source(inp.source)
        histories = {inp.id: self.store.history_manifest(inp.source) for inp in v.spec.inputs}
        asked = Period.parse(period) if isinstance(period, str) else period

        run_id = f"{scenario_id}-{self.store.next_run_seq(scenario_id):03d}"
        out_dir = self.folder.output_dir(run_id)
        self.store.add_run(
            RunRecord(
                id=run_id,
                scenario_id=scenario_id,
                scenario_version=v.number,
                scenario_name=v.spec.name,
                theme_id=v.theme_id,
                theme_version=v.theme_version,
                period=asked,
                period_given=asked is not None,
                source_versions={s.id: s.version for s in sources.values()},
                inputs_history=histories,
                trigger=trigger,
                started_at=_now(),
            )
        )
        req = RunRequest(
            scenario=v.spec,
            sources=[s.spec for s in sources.values()],
            histories=histories,
            theme=tv.pptx_uri,
            theme_roles=tv.roles,
            period=asked,
            output_dir=str(out_dir),
            workdir=str(workdir) if workdir else None,
            accept_cast_errors=accept_cast_errors,
            cache_dir=str(self.folder.root / "cache" / "engine"),
            temp_dir=str(self.folder.tmp / "engine"),
        )
        try:
            result = worker.run(req)
        except BaseException:
            self.store.update_run(run_id, status=RunStatus.INTERRUPTED, finished_at=_now())
            raise
        status = RunStatus.OK if result.ok else RunStatus.ERRORS if result.output_path else RunStatus.FAILED
        copy: Path | None = None
        if result.output_path and output is not None:
            try:
                copy = copy_report(Path(result.output_path), Path(output), result.issues)
            except (OSError, AgenError) as e:
                result.issues.append(
                    Issue(level=IssueLevel.WARNING, message=f"Копия отчёта в {output} не сделана: {e}")
                )
        return self.store.update_run(
            run_id,
            status=status,
            result=result,
            period=result.period or asked,
            output_uri=result.output_path,
            output_copy=str(copy) if copy else None,
            finished_at=_now(),
        )

    def rerun(self, run_id: str, *, output: str | Path | None = None, trigger: str = "cli") -> RunRecord:
        """Пересобрать отчёт за период прошлого запуска по текущей истории и текущей версии
        сценария (F-609a). Это не точная копия прошлого отчёта: загрузки, сделанные позже,
        тоже учитываются."""
        rec = self.store.get_run(run_id)
        if rec.period is None:
            raise AgenError(ErrorCode.PERIOD_INVALID, f"У запуска «{run_id}» не определён отчётный период")
        return self.run_scenario(rec.scenario_id, period=rec.period, output=output, trigger=trigger)

    def runs(self, scenario_id: str | None = None, limit: int | None = None) -> list[RunRecord]:
        """История запусков, новые первыми (F-609)."""
        if scenario_id is not None:
            self.store.get_scenario(scenario_id)
        return self.store.list_runs(scenario_id, limit)

    def run_record(self, run_id: str) -> RunRecord:
        return self.store.get_run(run_id)

    def run_output(self, run_id: str) -> Path:
        """Файл отчёта запуска: копия в выбранной папке, если она есть, иначе — в папке данных."""
        rec = self.store.get_run(run_id)
        for p in (rec.output_copy, rec.output_uri):
            if p and Path(p).is_file():
                return Path(p)
        raise AgenError(
            ErrorCode.FILE_NOT_FOUND,
            f"Отчёта запуска «{run_id}» нет" + (" (запуск не собрал отчёт)" if not rec.output_uri else ""),
            hint=f"Пересобрать за тот же период: agen runs rerun {run_id}",
        )

    def delete_run(self, run_id: str) -> None:
        """Удалить запись запуска и его отчёт в папке данных (копию в выбранной папке — нет)."""
        self._need_write()
        self.store.get_run(run_id)
        self.store.delete_run(run_id)
        self.blobs.delete(self.folder.output_dir(run_id).as_posix())

    # --- резервные копии ---------------------------------------------------------

    def backup(self, label: str = "manual") -> Path:
        """Резервная копия базы: источники, загрузки (записи о них), сценарии, шаблоны, запуски.
        Файлы загрузок и шаблонов не копируются: они неизменяемые и остаются на месте (F-804)."""
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "manual"
        return self.store.backup(self.folder.backups, safe)

    def backups(self) -> list[BackupInfo]:
        """Резервные копии, новые первыми."""
        files = [p for p in self.folder.backups.glob(BACKUP_GLOB) if p.is_file()]
        out = [BackupInfo(p, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime, UTC)) for p in files]
        return sorted(out, key=lambda b: (b.created, b.path.name), reverse=True)

    def restore_backup(self, path: str | Path) -> Path:
        """Вернуть базу из резервной копии. Текущая база перед этим сама копируется в
        ``backups`` (её путь возвращается). Если копия сделана старой версией приложения,
        база обновляется миграциями. Загрузки, файлы которых с тех пор удалены, из истории
        пропадут — их нужно загрузить снова."""
        self._need_write()
        src = Path(path)
        if not src.is_file():
            cand = self.folder.backups / src
            if not cand.is_file():
                raise AgenError(ErrorCode.FILE_NOT_FOUND, f"Резервной копии {src} нет", hint="Список: agen backup list")
            src = cand
        try:
            with sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True) as conn:
                conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.Error as e:
            raise AgenError(ErrorCode.DATA_FOLDER, f"{src.name} — не резервная копия базы Autogenerator: {e}") from e
        safety = self.backup("before-restore")
        self.store.close()
        db = self.folder.db_path
        for extra in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            extra.unlink(missing_ok=True)
        s, d = sqlite3.connect(src), sqlite3.connect(db)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        self.store = SqliteMetadataStore(db, self.folder.backups, self.folder.workspace)
        return safety
