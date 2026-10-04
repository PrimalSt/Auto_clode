"""Сценарии и их версии, запуски (разделы «Сценарии», «Код сценария», «Запуски»)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse, PlainTextResponse

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    Issue,
    JobContext,
    JobInfo,
    RunRecord,
    ScenarioRecord,
    ScenarioVersionRecord,
)
from autogenerator.home import Home, SavedScenario

from ..deps import HomeDep, StateDep, Wait, draft, file_reply, job_reply, period
from ..models import RerunIn, RunIn, SavedScenarioOut, ScenarioCopyIn, ScenarioIn

router = APIRouter(tags=["сценарии"])

Version = Annotated[int | None, Query(description="Версия; по умолчанию — текущая")]


def _saved(s: SavedScenario) -> SavedScenarioOut:
    return SavedScenarioOut(record=s.record, issues=s.issues)


def _save(home: Home, body: ScenarioIn, scenario_id: str | None) -> SavedScenario:
    if (body.text is None) == (body.spec is None):
        raise AgenError(ErrorCode.SPEC_INVALID, "Передайте сценарий текстом YAML (text) или JSON (spec)")
    sid = scenario_id or body.id
    if body.text is not None:
        return home.save_scenario_text(body.text, sid, theme=body.theme, comment=body.comment, theme_files=False)
    spec = draft(home, None, body.spec)
    return home.save_scenario(spec, sid, theme=body.theme, comment=body.comment, theme_files=False)


@router.get("/api/scenarios")
def list_scenarios(home: HomeDep) -> list[ScenarioRecord]:
    return home.scenarios()


@router.post("/api/scenarios", status_code=status.HTTP_201_CREATED)
def create_scenario(body: ScenarioIn, home: HomeDep, state: StateDep) -> SavedScenarioOut:
    """Новый сценарий (версия 1). Сценарий с ошибками тоже сохраняется — как черновик;
    ошибки возвращаются в ``issues``. Шаблон — id из папки данных."""
    sid = body.id
    if sid is not None and home.has_scenario(sid):
        raise AgenError(
            ErrorCode.ALREADY_EXISTS, f"Сценарий «{sid}» уже есть", hint="Новая версия: PUT /api/scenarios/{id}"
        )
    saved = _save(home, body, None)
    state.changed("scenarios", saved.record.id)
    return _saved(saved)


@router.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, home: HomeDep) -> ScenarioRecord:
    return home.scenario(scenario_id)


@router.put("/api/scenarios/{scenario_id}")
def save_scenario(scenario_id: str, body: ScenarioIn, home: HomeDep, state: StateDep) -> SavedScenarioOut:
    """Сохранить новую версию сценария."""
    home.scenario(scenario_id)
    saved = _save(home, body, scenario_id)
    state.changed("scenarios", scenario_id)
    return _saved(saved)


@router.delete("/api/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(scenario_id: str, home: HomeDep, state: StateDep) -> None:
    home.delete_scenario(scenario_id)
    state.changed("scenarios", scenario_id)
    state.changed("runs", scenario_id)


@router.get("/api/scenarios/{scenario_id}/versions")
def scenario_versions(scenario_id: str, home: HomeDep) -> list[ScenarioVersionRecord]:
    return home.scenario_versions(scenario_id)


@router.get("/api/scenarios/{scenario_id}/yaml", response_class=PlainTextResponse)
def scenario_yaml(scenario_id: str, home: HomeDep, version: Version = None) -> str:
    """Сценарий текстом YAML: как его сохранили, с комментариями."""
    return home.scenario_text(scenario_id, version)


@router.put("/api/scenarios/{scenario_id}/yaml")
def save_scenario_yaml(scenario_id: str, body: ScenarioIn, home: HomeDep, state: StateDep) -> SavedScenarioOut:
    """Сохранить текст YAML новой версией (то же, что PUT сценария с ``text``)."""
    return save_scenario(scenario_id, body, home, state)


@router.get("/api/scenarios/{scenario_id}/validate")
def validate_scenario(scenario_id: str, home: HomeDep, version: Version = None) -> list[Issue]:
    return home.validate_scenario(scenario_id, version)


@router.post("/api/scenarios/{scenario_id}/copy", status_code=status.HTTP_201_CREATED)
def copy_scenario(scenario_id: str, body: ScenarioCopyIn, home: HomeDep, state: StateDep) -> ScenarioRecord:
    rec = home.copy_scenario(scenario_id, body.id, body.name)
    state.changed("scenarios", rec.id)
    return rec


# --- запуски -------------------------------------------------------------------------


def _run_changed(r: RunRecord) -> list[tuple[str, str | None]]:
    return [("runs", r.scenario_id)]


@router.post("/api/scenarios/{scenario_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def run_scenario(scenario_id: str, body: RunIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Собрать отчёт (задание ``run``); итог — запись запуска с журналом."""

    def job(home: Home, _: JobContext) -> RunRecord:
        return home.run_scenario(
            scenario_id,
            period=period(body.period),
            version=body.version,
            output=body.output,
            accept_cast_errors=body.accept_cast_errors,
            trigger="app",
        )

    info = state.submit("run", job, title=f"Отчёт по сценарию «{scenario_id}»", changed=_run_changed)
    return job_reply(state, info, wait)


@router.get("/api/runs")
def list_runs(
    home: HomeDep,
    scenario: Annotated[str | None, Query(description="Только запуски этого сценария")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> list[RunRecord]:
    return home.runs(scenario, limit)


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, home: HomeDep) -> RunRecord:
    return home.run_record(run_id)


@router.get("/api/runs/{run_id}/output", response_class=FileResponse)
def run_output(run_id: str, home: HomeDep) -> FileResponse:
    """Готовый отчёт: копия в выбранной папке, если она есть, иначе — из папки данных."""
    return file_reply(
        home.run_output(run_id), "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )


@router.post("/api/runs/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
def rerun(run_id: str, body: RerunIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Пересобрать отчёт за период прошлого запуска по текущей истории."""

    def job(home: Home, _: JobContext) -> RunRecord:
        return home.rerun(run_id, output=body.output, trigger="app")

    info = state.submit("run", job, title=f"Пересборка запуска «{run_id}»", changed=_run_changed)
    return job_reply(state, info, wait)


@router.delete("/api/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: str, home: HomeDep, state: StateDep) -> None:
    rec = home.run_record(run_id)
    home.delete_run(run_id)
    state.changed("runs", rec.scenario_id)
