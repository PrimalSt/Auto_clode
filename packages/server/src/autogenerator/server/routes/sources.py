"""Источники, загрузки и история (разделы «Источники», «Структура источника», «Проверка загрузки»)."""

from __future__ import annotations

from fastapi import APIRouter, status

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    JobContext,
    JobInfo,
    SourceRecord,
    SourceVersionRecord,
    UploadRecord,
)
from autogenerator.home import ColumnUsage, Home, UploadOutcome

from ..deps import HomeDep, StateDep, Wait, job_reply, period
from ..models import (
    ColumnUsageOut,
    HistoryOut,
    SourceDraftIn,
    SourceDraftOut,
    SourceIn,
    SourcesImportIn,
    UploadIn,
    UploadOut,
    UploadPatch,
)

router = APIRouter(tags=["источники"])


@router.get("/api/sources")
def list_sources(home: HomeDep) -> list[SourceRecord]:
    return home.sources()


@router.post("/api/sources/draft", status_code=status.HTTP_202_ACCEPTED)
def draft_source(body: SourceDraftIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Черновик источника по образцу выгрузки: структура, типы, столбец периода (не сохраняется).
    Итог задания — ``SourceDraftOut``."""

    def job(home: Home, _: JobContext) -> SourceDraftOut:
        spec, snap = home.draft_source(
            body.path,
            body.id,
            body.name,
            body.period_column,
            body.period_type,
            body.options,
            body.format,
            period_from=body.period_from,
        )
        return SourceDraftOut(source=spec, snapshot=snap)

    return job_reply(state, state.submit("draft_source", job, title=f"Структура выгрузки для «{body.id}»"), wait)


@router.post("/api/sources", status_code=status.HTTP_201_CREATED)
def create_source(body: SourceIn, home: HomeDep, state: StateDep) -> SourceRecord:
    rec = home.create_source(body.spec, body.comment)
    state.changed("sources", rec.id)
    return rec


@router.post("/api/sources/import")
def import_sources(body: SourcesImportIn, home: HomeDep, state: StateDep) -> list[SourceRecord]:
    """Источники из YAML: новые создаются, существующие получают новую версию."""
    out = home.import_sources(body.path, body.comment, body.force)
    state.changed("sources")
    return out


@router.get("/api/sources/{source_id}")
def get_source(source_id: str, home: HomeDep) -> SourceRecord:
    return home.source(source_id)


@router.put("/api/sources/{source_id}")
def update_source(source_id: str, body: SourceIn, home: HomeDep, state: StateDep) -> SourceRecord:
    """Новая версия настроек источника."""
    if body.spec.id != source_id:
        raise AgenError(ErrorCode.SPEC_INVALID, f"id источника в настройках «{body.spec.id}», а в адресе «{source_id}»")
    rec = home.update_source(body.spec, body.comment, body.force)
    state.changed("sources", source_id)
    return rec


@router.delete("/api/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: str, home: HomeDep, state: StateDep) -> None:
    home.delete_source(source_id)
    state.changed("sources", source_id)


@router.get("/api/sources/{source_id}/versions")
def source_versions(source_id: str, home: HomeDep) -> list[SourceVersionRecord]:
    return home.source_versions(source_id)


@router.get("/api/sources/{source_id}/usage")
def column_usage(source_id: str, home: HomeDep) -> ColumnUsageOut:
    """Какие столбцы источника нужны сохранённым сценариям и кому именно."""
    u: ColumnUsage = home.column_usage(source_id)
    return ColumnUsageOut(required=u.required, dependents=u.dependents)


@router.get("/api/sources/{source_id}/history")
def history(source_id: str, home: HomeDep) -> HistoryOut:
    """Загрузки, шкала покрытия и занимаемое место."""
    uploads = home.uploads(source_id)
    coverage = home.coverage(source_id) if any(u.status == "active" for u in uploads) else None
    return HistoryOut(uploads=uploads, coverage=coverage, disk_usage=home.disk_usage(source_id))


@router.get("/api/sources/{source_id}/uploads")
def list_uploads(source_id: str, home: HomeDep) -> list[UploadRecord]:
    return home.uploads(source_id)


@router.post("/api/sources/{source_id}/uploads", status_code=status.HTTP_202_ACCEPTED)
def add_upload(source_id: str, body: UploadIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Загрузить выгрузку (задание ``upload``): ход — в событиях, итог — запись загрузки,
    предупреждения и запомненные названия столбцов. Ошибка ``schema_review`` несёт в
    ``details.files`` сверку по файлам с кандидатами — для экрана сопоставления."""

    def job(home: Home, ctx: JobContext) -> UploadOut:
        out: UploadOutcome = home.upload(
            source_id,
            body.paths,
            options=body.options,
            period=period(body.period),
            overlap_policy=body.overlap_policy,
            accept_cast_errors=body.accept_cast_errors,
            mapping=body.mapping,
            declined=body.declined,
            accept_mapping=body.accept_mapping,
            force=body.force,
            profile=body.profile,
            progress=ctx.progress_callback,
            cancelled=ctx.cancelled,
        )
        return UploadOut(
            record=out.record, issues=out.issues, remembered=out.remembered, reconcile=out.result.reconcile
        )

    names = " + ".join(p.replace("\\", "/").rsplit("/", 1)[-1] for p in body.paths)
    info = state.submit(
        "upload",
        job,
        title=f"Загрузка {names} в «{source_id}»",
        changed=lambda r: [("uploads", source_id), *([("sources", source_id)] if r.remembered else [])],
    )
    return job_reply(state, info, wait)


@router.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str, home: HomeDep) -> UploadRecord:
    return home.upload_record(upload_id)


@router.patch("/api/uploads/{upload_id}")
def patch_upload(upload_id: str, body: UploadPatch, home: HomeDep, state: StateDep) -> UploadRecord:
    """Принять (``active``), исключить из истории (``excluded``), вернуть; сменить период и правило."""
    rec = home.upload_record(upload_id)
    given = body.model_fields_set
    if "status" in given and body.status is not None:
        rec = home.set_upload_status(upload_id, body.status)
    if "period" in given and body.period is not None:
        p = period(body.period)
        assert p is not None
        rec = home.set_upload_period(upload_id, p)
    if "overlap_policy" in given:
        rec = home.set_upload_policy(upload_id, body.overlap_policy)
    state.changed("uploads", rec.source_id)
    return rec


@router.delete("/api/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(upload_id: str, home: HomeDep, state: StateDep) -> None:
    rec = home.upload_record(upload_id)
    home.delete_upload(upload_id)
    state.changed("uploads", rec.source_id)
