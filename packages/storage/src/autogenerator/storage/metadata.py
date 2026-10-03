"""Метаданные в SQLite (режим WAL) через SQLAlchemy 2 (ARCHITECTURE.md, раздел 9):
источники, загрузки, сценарии, шаблоны оформления, запуски и их версии.

Схема создаётся и обновляется миграциями Alembic при открытии базы; перед миграцией
существующей базы делается резервная копия в ``backups``. Пишет метаданные один процесс,
взявший блокировку папки данных (``DataFolder.lock``); исполнители базу не открывают.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, delete, event, func, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from autogenerator.contracts import (
    DEFAULT_WORKSPACE,
    AgenError,
    CastIssue,
    ColumnProfile,
    ErrorCode,
    HistoryManifest,
    OverlapPolicy,
    Period,
    PeriodUnit,
    ReadOptions,
    RunRecord,
    RunResult,
    RunStatus,
    ScenarioInputRef,
    ScenarioRecord,
    ScenarioSpec,
    ScenarioVersionRecord,
    SchemaSnapshot,
    SourceRecord,
    SourceSpec,
    SourceVersionRecord,
    ThemeManifest,
    ThemeRecord,
    ThemeVersionRecord,
    UploadRecord,
    UploadRef,
    UploadStatus,
)

from .tables import (
    runs,
    scenario_inputs,
    scenario_versions,
    scenarios,
    source_versions,
    sources,
    theme_versions,
    themes,
    uploads,
)

MIGRATIONS = Path(__file__).parent / "migrations"


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _utc(v: datetime) -> datetime:
    return v if v.tzinfo else v.replace(tzinfo=UTC)


def _json(v: Any) -> Any:
    return json.loads(json.dumps(v, ensure_ascii=False, default=str))


def _alembic_config(conn: Connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["connection"] = conn
    return cfg


class SqliteMetadataStore:
    """``MetadataStore`` на SQLite."""

    def __init__(self, db_path: str | Path, backups: str | Path | None = None, workspace: str = DEFAULT_WORKSPACE):
        self.path = Path(db_path)
        self.workspace = workspace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        self.engine: Engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            json_serializer=lambda o: json.dumps(o, ensure_ascii=False),
        )
        event.listen(self.engine, "connect", _pragmas)
        self._migrate(existed, Path(backups) if backups else None)

    # --- схема -------------------------------------------------------------------

    def _migrate(self, existed: bool, backups: Path | None) -> None:
        with self.engine.connect() as conn:
            cfg = _alembic_config(conn)
            head = ScriptDirectory.from_config(cfg).get_current_head()
            current = MigrationContext.configure(conn).get_current_revision()
            if current == head:
                return
            if existed and backups is not None:
                self.backup(backups, f"before-{head}")
            command.upgrade(cfg, "head")
            conn.commit()

    def schema_revision(self) -> str | None:
        with self.engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()

    def backup(self, folder: str | Path, label: str = "manual") -> Path:
        """Резервная копия базы средствами SQLite (корректна и при открытой базе)."""
        out_dir = Path(folder)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = out_dir / f"autogenerator-{stamp}-{label}.sqlite"
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return out

    def close(self) -> None:
        self.engine.dispose()

    # --- источники ---------------------------------------------------------------

    def list_sources(self) -> list[SourceRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(sources).where(sources.c.workspace_id == self.workspace).order_by(sources.c.id)
            ).mappings()
            return [self._source(conn, r) for r in rows]

    def get_source(self, source_id: str) -> SourceRecord:
        with self.engine.connect() as conn:
            row = conn.execute(select(sources).where(sources.c.id == source_id)).mappings().first()
            if row is None:
                known = ", ".join(r.id for r in self.list_sources()) or "нет"
                raise AgenError(
                    ErrorCode.NOT_FOUND,
                    f"Источника «{source_id}» нет в папке данных (есть: {known})",
                    hint="Создайте его: agen source create <id> --from выгрузка.csv",
                )
            return self._source(conn, row)

    def has_source(self, source_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(sources.c.id).where(sources.c.id == source_id)).first() is not None

    def _source(self, conn: Connection, row: RowMapping) -> SourceRecord:
        spec: dict[str, Any] = conn.execute(
            select(source_versions.c.spec).where(
                source_versions.c.source_id == row["id"], source_versions.c.number == row["current_version"]
            )
        ).scalar_one()
        return SourceRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            spec=SourceSpec.model_validate(spec),
            version=row["current_version"],
            created_at=_utc(row["created_at"]),
        )

    def source_versions(self, source_id: str) -> list[SourceVersionRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(source_versions)
                .where(source_versions.c.source_id == source_id)
                .order_by(source_versions.c.number)
            ).mappings()
            return [
                SourceVersionRecord(
                    source_id=r["source_id"],
                    number=r["number"],
                    spec=SourceSpec.model_validate(r["spec"]),
                    comment=r["comment"],
                    created_at=_utc(r["created_at"]),
                )
                for r in rows
            ]

    def create_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord:
        if self.has_source(spec.id):
            raise AgenError(
                ErrorCode.ALREADY_EXISTS,
                f"Источник «{spec.id}» уже есть",
                hint="Изменить настройки: agen source edit; другой id — agen source create <новый id>.",
            )
        now = _now()
        spec = spec.model_copy(update={"version": 1})
        with self.engine.begin() as conn:
            conn.execute(
                insert(sources).values(
                    id=spec.id, workspace_id=self.workspace, name=spec.name, current_version=1, created_at=now
                )
            )
            conn.execute(
                insert(source_versions).values(
                    source_id=spec.id,
                    number=1,
                    spec=spec.model_dump(mode="json"),
                    comment=comment or "создан",
                    created_at=now,
                )
            )
        return self.get_source(spec.id)

    def update_source(self, spec: SourceSpec, comment: str = "") -> SourceRecord:
        """Новая версия настроек (F-163). Без изменений версия не создаётся."""
        current = self.get_source(spec.id)
        if spec.model_dump(exclude={"version"}) == current.spec.model_dump(exclude={"version"}):
            return current
        number = current.version + 1
        spec = spec.model_copy(update={"version": number})
        with self.engine.begin() as conn:
            conn.execute(
                insert(source_versions).values(
                    source_id=spec.id,
                    number=number,
                    spec=spec.model_dump(mode="json"),
                    comment=comment,
                    created_at=_now(),
                )
            )
            conn.execute(update(sources).where(sources.c.id == spec.id).values(current_version=number, name=spec.name))
        return self.get_source(spec.id)

    def delete_source(self, source_id: str) -> None:
        self.get_source(source_id)
        users = sorted({r.scenario_id or "" for r in self.scenarios_using_source(source_id)})
        if users:
            raise AgenError(
                ErrorCode.IN_USE,
                f"Источник «{source_id}» используют сценарии: {', '.join(users)}",
                hint="Сначала уберите его из сценариев или удалите их: agen scenario delete <id>.",
            )
        with self.engine.begin() as conn:
            conn.execute(delete(uploads).where(uploads.c.source_id == source_id))
            conn.execute(delete(source_versions).where(source_versions.c.source_id == source_id))
            conn.execute(delete(sources).where(sources.c.id == source_id))

    # --- загрузки ----------------------------------------------------------------

    def list_uploads(self, source_id: str) -> list[UploadRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(uploads).where(uploads.c.source_id == source_id).order_by(uploads.c.seq)
            ).mappings()
            return [_upload(r) for r in rows]

    def get_upload(self, upload_id: str) -> UploadRecord:
        with self.engine.connect() as conn:
            row = conn.execute(select(uploads).where(uploads.c.id == upload_id)).mappings().first()
        if row is None:
            raise AgenError(ErrorCode.NOT_FOUND, f"Загрузки «{upload_id}» нет")
        return _upload(row)

    def find_uploads(self, sha256: str) -> list[UploadRecord]:
        """Загрузки того же файла (по SHA-256 содержимого)."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(uploads).where(uploads.c.sha256 == sha256).order_by(uploads.c.seq)).mappings()
            return [_upload(r) for r in rows]

    def next_upload_seq(self, source_id: str) -> int:
        with self.engine.connect() as conn:
            top = conn.execute(select(func.max(uploads.c.seq)).where(uploads.c.source_id == source_id)).scalar()
        return int(top or 0) + 1

    def add_upload(self, record: UploadRecord) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(uploads).values(**_upload_row(record)))

    def update_upload(self, upload_id: str, **fields: Any) -> UploadRecord:
        rec = self.get_upload(upload_id).model_copy(update=fields)
        row = _upload_row(UploadRecord.model_validate(rec.model_dump()))
        with self.engine.begin() as conn:
            conn.execute(update(uploads).where(uploads.c.id == upload_id).values(**row))
        return self.get_upload(upload_id)

    def delete_upload(self, upload_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(uploads).where(uploads.c.id == upload_id))

    def history_manifest(self, source_id: str) -> HistoryManifest:
        spec = self.get_source(source_id).spec
        refs = [
            UploadRef(
                id=u.id,
                seq=u.seq,
                uri=u.data_uri,
                period=u.period,
                rows=u.rows,
                status=u.status,
                original_name=u.original_name,
                overlap_policy=u.overlap_policy,
                uploaded_at=u.uploaded_at,
            )
            for u in self.list_uploads(source_id)
        ]
        return HistoryManifest.for_source(spec, refs)

    # --- сценарии ----------------------------------------------------------------

    def list_scenarios(self) -> list[ScenarioRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(scenarios).where(scenarios.c.workspace_id == self.workspace).order_by(scenarios.c.id)
            ).mappings()
            return [self._scenario(conn, r) for r in rows]

    def has_scenario(self, scenario_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(scenarios.c.id).where(scenarios.c.id == scenario_id)).first() is not None

    def get_scenario(self, scenario_id: str) -> ScenarioRecord:
        with self.engine.connect() as conn:
            row = conn.execute(select(scenarios).where(scenarios.c.id == scenario_id)).mappings().first()
            if row is None:
                known = ", ".join(r.id for r in self.list_scenarios()) or "нет"
                raise AgenError(
                    ErrorCode.NOT_FOUND,
                    f"Сценария «{scenario_id}» нет в папке данных (есть: {known})",
                    hint="Добавьте его: agen scenario add сценарий.yaml",
                )
            return self._scenario(conn, row)

    def _scenario(self, conn: Connection, row: RowMapping) -> ScenarioRecord:
        ver = (
            conn.execute(
                select(scenario_versions).where(
                    scenario_versions.c.scenario_id == row["id"], scenario_versions.c.number == row["current_version"]
                )
            )
            .mappings()
            .one()
        )
        inputs = conn.execute(
            select(scenario_inputs)
            .where(scenario_inputs.c.scenario_id == row["id"])
            .order_by(scenario_inputs.c.input_id)
        ).mappings()
        return ScenarioRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            version=row["current_version"],
            current=_scenario_version(ver),
            inputs=[
                ScenarioInputRef(input_id=i["input_id"], source_id=i["source_id"], main=i["is_main"]) for i in inputs
            ],
            created_at=_utc(row["created_at"]),
        )

    def save_scenario(
        self,
        scenario_id: str,
        spec: ScenarioSpec,
        text: str | None = None,
        theme: tuple[str, int] | None = None,
        comment: str = "",
    ) -> ScenarioRecord:
        """Сохранить сценарий: новый — с версией 1, существующий — новой версией (F-503).
        Если ни сценарий, ни его текст, ни версия шаблона не изменились, версия не создаётся."""
        now = _now()
        spec_json = spec.model_dump(mode="json")
        theme_id, theme_version = theme if theme else (None, None)
        with self.engine.begin() as conn:
            row = conn.execute(select(scenarios).where(scenarios.c.id == scenario_id)).mappings().first()
            if row is None:
                number = 1
                conn.execute(
                    insert(scenarios).values(
                        id=scenario_id, workspace_id=self.workspace, name=spec.name, current_version=1, created_at=now
                    )
                )
            else:
                cur = (
                    conn.execute(
                        select(scenario_versions).where(
                            scenario_versions.c.scenario_id == scenario_id,
                            scenario_versions.c.number == row["current_version"],
                        )
                    )
                    .mappings()
                    .one()
                )
                same = (
                    cur["spec"] == spec_json
                    and (cur["text"] or None) == (text or None)
                    and cur["theme_id"] == theme_id
                    and cur["theme_version"] == theme_version
                )
                if same:
                    return self._scenario(conn, row)
                number = row["current_version"] + 1
                conn.execute(
                    update(scenarios)
                    .where(scenarios.c.id == scenario_id)
                    .values(current_version=number, name=spec.name)
                )
            conn.execute(
                insert(scenario_versions).values(
                    scenario_id=scenario_id,
                    number=number,
                    spec=spec_json,
                    text=text,
                    theme_id=theme_id,
                    theme_version=theme_version,
                    comment=comment or ("создан" if number == 1 else ""),
                    created_at=now,
                )
            )
            conn.execute(delete(scenario_inputs).where(scenario_inputs.c.scenario_id == scenario_id))
            main = spec.main_input.id
            for inp in spec.inputs:
                conn.execute(
                    insert(scenario_inputs).values(
                        scenario_id=scenario_id, input_id=inp.id, source_id=inp.source, is_main=inp.id == main
                    )
                )
            row = conn.execute(select(scenarios).where(scenarios.c.id == scenario_id)).mappings().one()
            return self._scenario(conn, row)

    def scenario_versions(self, scenario_id: str) -> list[ScenarioVersionRecord]:
        self.get_scenario(scenario_id)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(scenario_versions)
                .where(scenario_versions.c.scenario_id == scenario_id)
                .order_by(scenario_versions.c.number)
            ).mappings()
            return [_scenario_version(r) for r in rows]

    def get_scenario_version(self, scenario_id: str, number: int) -> ScenarioVersionRecord:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(scenario_versions).where(
                        scenario_versions.c.scenario_id == scenario_id, scenario_versions.c.number == number
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            self.get_scenario(scenario_id)
            raise AgenError(ErrorCode.NOT_FOUND, f"У сценария «{scenario_id}» нет версии {number}")
        return _scenario_version(row)

    def scenarios_using_source(self, source_id: str) -> list[ScenarioInputRef]:
        """Входы сценариев, которые берут историю этого источника."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(scenario_inputs)
                .where(scenario_inputs.c.source_id == source_id)
                .order_by(scenario_inputs.c.scenario_id, scenario_inputs.c.input_id)
            ).mappings()
            return [
                ScenarioInputRef(
                    input_id=r["input_id"], source_id=source_id, main=r["is_main"], scenario_id=r["scenario_id"]
                )
                for r in rows
            ]

    def scenarios_using_theme(self, theme_id: str) -> list[ScenarioRecord]:
        return [s for s in self.list_scenarios() if s.current.theme_id == theme_id]

    def delete_scenario(self, scenario_id: str) -> None:
        self.get_scenario(scenario_id)
        with self.engine.begin() as conn:
            conn.execute(delete(runs).where(runs.c.scenario_id == scenario_id))
            conn.execute(delete(scenario_inputs).where(scenario_inputs.c.scenario_id == scenario_id))
            conn.execute(delete(scenario_versions).where(scenario_versions.c.scenario_id == scenario_id))
            conn.execute(delete(scenarios).where(scenarios.c.id == scenario_id))

    # --- шаблоны оформления ------------------------------------------------------

    def list_themes(self) -> list[ThemeRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(themes).where(themes.c.workspace_id == self.workspace).order_by(themes.c.id)
            ).mappings()
            return [self._theme(conn, r) for r in rows]

    def has_theme(self, theme_id: str) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(themes.c.id).where(themes.c.id == theme_id)).first() is not None

    def get_theme(self, theme_id: str) -> ThemeRecord:
        with self.engine.connect() as conn:
            row = conn.execute(select(themes).where(themes.c.id == theme_id)).mappings().first()
            if row is None:
                known = ", ".join(r.id for r in self.list_themes()) or "нет"
                raise AgenError(
                    ErrorCode.NOT_FOUND,
                    f"Шаблона оформления «{theme_id}» нет в папке данных (есть: {known})",
                    hint="Загрузите его: agen theme import шаблон.pptx",
                )
            return self._theme(conn, row)

    def _theme(self, conn: Connection, row: RowMapping) -> ThemeRecord:
        ver = (
            conn.execute(
                select(theme_versions).where(
                    theme_versions.c.theme_id == row["id"], theme_versions.c.number == row["current_version"]
                )
            )
            .mappings()
            .one()
        )
        return ThemeRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            version=row["current_version"],
            current=_theme_version(ver),
            created_at=_utc(row["created_at"]),
        )

    def next_theme_version(self, theme_id: str) -> int:
        with self.engine.connect() as conn:
            top = conn.execute(
                select(func.max(theme_versions.c.number)).where(theme_versions.c.theme_id == theme_id)
            ).scalar()
        return int(top or 0) + 1

    def add_theme_version(self, record: ThemeVersionRecord, name: str | None = None) -> ThemeRecord:
        """Новая версия шаблона (``record.number`` — из ``next_theme_version``); первая
        версия создаёт сам шаблон с именем ``name``."""
        with self.engine.begin() as conn:
            row = conn.execute(select(themes).where(themes.c.id == record.theme_id)).mappings().first()
            if row is None:
                conn.execute(
                    insert(themes).values(
                        id=record.theme_id,
                        workspace_id=self.workspace,
                        name=name or record.original_name,
                        current_version=record.number,
                        created_at=record.imported_at,
                    )
                )
            else:
                values: dict[str, Any] = {"current_version": record.number}
                if name:
                    values["name"] = name
                conn.execute(update(themes).where(themes.c.id == record.theme_id).values(**values))
            conn.execute(
                insert(theme_versions).values(
                    theme_id=record.theme_id,
                    number=record.number,
                    pptx_uri=record.pptx_uri,
                    sha256=record.sha256,
                    original_name=record.original_name,
                    manifest=record.manifest.model_dump(mode="json"),
                    roles=dict(record.roles),
                    comment=record.comment,
                    imported_at=record.imported_at,
                )
            )
            row = conn.execute(select(themes).where(themes.c.id == record.theme_id)).mappings().one()
            return self._theme(conn, row)

    def theme_versions(self, theme_id: str) -> list[ThemeVersionRecord]:
        self.get_theme(theme_id)
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(theme_versions).where(theme_versions.c.theme_id == theme_id).order_by(theme_versions.c.number)
            ).mappings()
            return [_theme_version(r) for r in rows]

    def get_theme_version(self, theme_id: str, number: int) -> ThemeVersionRecord:
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(theme_versions).where(
                        theme_versions.c.theme_id == theme_id, theme_versions.c.number == number
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            self.get_theme(theme_id)
            raise AgenError(ErrorCode.NOT_FOUND, f"У шаблона «{theme_id}» нет версии {number}")
        return _theme_version(row)

    def find_theme_versions(self, sha256: str) -> list[ThemeVersionRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(theme_versions).where(theme_versions.c.sha256 == sha256).order_by(theme_versions.c.id)
            ).mappings()
            return [_theme_version(r) for r in rows]

    def delete_theme(self, theme_id: str) -> None:
        self.get_theme(theme_id)
        with self.engine.begin() as conn:
            conn.execute(delete(theme_versions).where(theme_versions.c.theme_id == theme_id))
            conn.execute(delete(themes).where(themes.c.id == theme_id))

    # --- запуски -----------------------------------------------------------------

    def next_run_seq(self, scenario_id: str) -> int:
        """Номер следующего запуска; номера удалённых запусков не повторяются."""
        with self.engine.connect() as conn:
            top = conn.execute(select(func.max(runs.c.seq)).where(runs.c.scenario_id == scenario_id)).scalar()
            last = conn.execute(select(scenarios.c.last_run_seq).where(scenarios.c.id == scenario_id)).scalar()
        return max(int(top or 0), int(last or 0)) + 1

    def add_run(self, record: RunRecord) -> None:
        seq = int(record.id.rsplit("-", 1)[1])
        with self.engine.begin() as conn:
            conn.execute(insert(runs).values(seq=seq, **_run_row(record)))
            last = conn.execute(select(scenarios.c.last_run_seq).where(scenarios.c.id == record.scenario_id)).scalar()
            if last is not None and seq > last:
                conn.execute(update(scenarios).where(scenarios.c.id == record.scenario_id).values(last_run_seq=seq))

    def update_run(self, run_id: str, **fields: Any) -> RunRecord:
        rec = self.get_run(run_id).model_copy(update=fields)
        row = _run_row(RunRecord.model_validate(rec.model_dump()))
        with self.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.id == run_id).values(**row))
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        if row is None:
            raise AgenError(ErrorCode.NOT_FOUND, f"Запуска «{run_id}» нет", hint="Список запусков: agen runs")
        return _run(row)

    def list_runs(self, scenario_id: str | None = None, limit: int | None = None) -> list[RunRecord]:
        """Запуски, новые первыми."""
        q = select(runs).order_by(runs.c.started_at.desc(), runs.c.seq.desc())
        if scenario_id is not None:
            q = q.where(runs.c.scenario_id == scenario_id)
        if limit is not None:
            q = q.limit(limit)
        with self.engine.connect() as conn:
            return [_run(r) for r in conn.execute(q).mappings()]

    def delete_run(self, run_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(runs).where(runs.c.id == run_id))

    def interrupt_running(self) -> list[str]:
        """Запуски, которые остались «идут» после сбоя, помечаются прерванными (при старте)."""
        with self.engine.begin() as conn:
            ids = [r[0] for r in conn.execute(select(runs.c.id).where(runs.c.status == RunStatus.RUNNING.value))]
            if ids:
                conn.execute(
                    update(runs)
                    .where(runs.c.id.in_(ids))
                    .values(status=RunStatus.INTERRUPTED.value, finished_at=_now())
                )
        return ids


def _scenario_version(r: RowMapping) -> ScenarioVersionRecord:
    return ScenarioVersionRecord(
        scenario_id=r["scenario_id"],
        number=r["number"],
        spec=ScenarioSpec.model_validate(r["spec"]),
        text=r["text"],
        theme_id=r["theme_id"],
        theme_version=r["theme_version"],
        comment=r["comment"],
        created_at=_utc(r["created_at"]),
    )


def _theme_version(r: RowMapping) -> ThemeVersionRecord:
    return ThemeVersionRecord(
        theme_id=r["theme_id"],
        number=r["number"],
        pptx_uri=r["pptx_uri"],
        sha256=r["sha256"],
        original_name=r["original_name"],
        manifest=ThemeManifest.model_validate(r["manifest"]),
        roles=r["roles"] or {},
        comment=r["comment"],
        imported_at=_utc(r["imported_at"]),
    )


def _run_row(r: RunRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "scenario_id": r.scenario_id,
        "scenario_version": r.scenario_version,
        "scenario_name": r.scenario_name,
        "theme_id": r.theme_id,
        "theme_version": r.theme_version,
        "period": r.period.model_dump(mode="json") if r.period else None,
        "period_given": r.period_given,
        "source_versions": dict(r.source_versions),
        "inputs_history": {k: v.model_dump(mode="json") for k, v in r.inputs_history.items()},
        "status": r.status.value,
        "result": r.result.model_dump(mode="json") if r.result else None,
        "output_uri": r.output_uri,
        "output_copy": r.output_copy,
        "trigger": r.trigger,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


def _run(r: RowMapping) -> RunRecord:
    return RunRecord(
        id=r["id"],
        scenario_id=r["scenario_id"],
        scenario_version=r["scenario_version"],
        scenario_name=r["scenario_name"],
        theme_id=r["theme_id"],
        theme_version=r["theme_version"],
        period=Period.model_validate(r["period"]) if r["period"] else None,
        period_given=r["period_given"],
        source_versions=r["source_versions"],
        inputs_history={k: HistoryManifest.model_validate(v) for k, v in r["inputs_history"].items()},
        status=RunStatus(r["status"]),
        result=RunResult.model_validate(r["result"]) if r["result"] else None,
        output_uri=r["output_uri"],
        output_copy=r["output_copy"],
        trigger=r["trigger"],
        started_at=_utc(r["started_at"]),
        finished_at=_utc(r["finished_at"]) if r["finished_at"] else None,
    )


def _pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def _upload_row(r: UploadRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "source_id": r.source_id,
        "seq": r.seq,
        "source_version": r.source_version,
        "original_name": r.original_name,
        "sha256": r.sha256,
        "size": r.size,
        "format": r.format,
        "options": r.options.model_dump(mode="json") if r.options else None,
        "raw_uri": r.raw_uri,
        "data_uri": r.data_uri,
        "rejects_uri": r.rejects_uri,
        "schema_snapshot": r.schema_snapshot.model_dump(mode="json") if r.schema_snapshot else None,
        "cast_report": [c.model_dump(mode="json") for c in r.cast_report],
        "mapping": _json(r.mapping),
        "profile": {k: v.model_dump(mode="json") for k, v in r.profile.items()},
        "period_start": r.period.start,
        "period_end_exclusive": r.period.end_exclusive,
        "period_unit": r.period.unit.value,
        "period_label": r.period.key,
        "period_from_data": r.period_from_data.model_dump(mode="json") if r.period_from_data else None,
        "rows": r.rows,
        "rows_outside_period": r.rows_outside_period,
        "null_period_rows": r.null_period_rows,
        "status": r.status.value,
        "review_reasons": list(r.review_reasons),
        "overlap_policy": r.overlap_policy.value if r.overlap_policy else None,
        "data_bytes": r.data_bytes,
        "uploaded_at": r.uploaded_at,
    }


def _date(v: date | str) -> date:
    return v if isinstance(v, date) else date.fromisoformat(v)


def _upload(r: RowMapping) -> UploadRecord:
    return UploadRecord(
        id=r["id"],
        source_id=r["source_id"],
        seq=r["seq"],
        source_version=r["source_version"],
        original_name=r["original_name"],
        sha256=r["sha256"],
        size=r["size"],
        format=r["format"],
        options=ReadOptions.model_validate(r["options"]) if r["options"] else None,
        raw_uri=r["raw_uri"],
        data_uri=r["data_uri"],
        rejects_uri=r["rejects_uri"],
        schema_snapshot=SchemaSnapshot.model_validate(r["schema_snapshot"]) if r["schema_snapshot"] else None,
        cast_report=[CastIssue.model_validate(c) for c in r["cast_report"]],
        mapping=r["mapping"],
        profile={k: ColumnProfile.model_validate(v) for k, v in r["profile"].items()},
        period=Period(
            start=_date(r["period_start"]),
            end_exclusive=_date(r["period_end_exclusive"]),
            unit=PeriodUnit(r["period_unit"]),
        ),
        period_from_data=Period.model_validate(r["period_from_data"]) if r["period_from_data"] else None,
        rows=r["rows"],
        rows_outside_period=r["rows_outside_period"],
        null_period_rows=r["null_period_rows"],
        status=UploadStatus(r["status"]),
        review_reasons=r["review_reasons"],
        overlap_policy=OverlapPolicy(r["overlap_policy"]) if r["overlap_policy"] else None,
        data_bytes=r["data_bytes"],
        uploaded_at=_utc(r["uploaded_at"]),
    )
