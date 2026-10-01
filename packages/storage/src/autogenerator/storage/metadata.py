"""Метаданные в SQLite (режим WAL) через SQLAlchemy 2 (ARCHITECTURE.md, раздел 9).

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
    SchemaSnapshot,
    SourceRecord,
    SourceSpec,
    SourceVersionRecord,
    UploadRecord,
    UploadRef,
    UploadStatus,
)

from .tables import source_versions, sources, uploads

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
        return HistoryManifest(
            source_id=spec.id,
            source_version=spec.version,
            period_column=spec.period_column,
            period_type=spec.period_type,
            overlap_policy=spec.overlap_policy,
            keys=spec.keys,
            columns=spec.dtypes,
            uploads=refs,
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
