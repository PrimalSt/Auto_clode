"""Шаблоны оформления (раздел «Оформление»)."""

from __future__ import annotations

from fastapi import APIRouter, status

from autogenerator.contracts import JobContext, JobInfo, ThemeRecord, ThemeVersionRecord
from autogenerator.home import Home, ThemeImport

from ..deps import HomeDep, StateDep, Wait, job_reply
from ..models import ExportIn, ReimportIn, RolesIn, ThemeImportOut, ThemeIn
from ..state import ServerState

router = APIRouter(tags=["оформление"])


def _out(t: ThemeImport) -> ThemeImportOut:
    return ThemeImportOut(record=t.record, skipped=t.skipped, matched=t.matched, scenarios=t.scenarios, lost=t.lost)


def _changed(t: ThemeImportOut) -> list[tuple[str, str | None]]:
    return [("themes", t.record.id), *([("scenarios", None)] if t.scenarios else [])]


def _import(
    state: ServerState, path: str, theme_id: str | None, name: str | None, comment: str, wait: float | None
) -> JobInfo:
    def job(home: Home, _: JobContext) -> ThemeImportOut:
        return _out(home.import_theme(path, theme_id, name=name, comment=comment))

    title = f"Шаблон {path.replace(chr(92), '/').rsplit('/', 1)[-1]}"
    return job_reply(state, state.submit("import_theme", job, title=title, changed=_changed), wait)


@router.get("/api/themes")
def list_themes(home: HomeDep) -> list[ThemeRecord]:
    return home.themes()


@router.post("/api/themes", status_code=status.HTTP_202_ACCEPTED)
def import_theme(body: ThemeIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Загрузить шаблон .pptx/.potx (задание ``import_theme``): проверка, роли макетов,
    слайды-образцы. Итог — ``ThemeImportOut``. Тот же файл второй раз не загружается."""
    return _import(state, body.path, body.id, body.name, body.comment, wait)


@router.get("/api/themes/{theme_id}")
def get_theme(theme_id: str, home: HomeDep) -> ThemeRecord:
    """Шаблон с текущей версией: макеты, роли, слайды-образцы, их метки, графики и таблицы."""
    return home.theme(theme_id)


@router.get("/api/themes/{theme_id}/versions")
def theme_versions(theme_id: str, home: HomeDep) -> list[ThemeVersionRecord]:
    return home.theme_versions(theme_id)


@router.get("/api/themes/{theme_id}/versions/{number}")
def theme_version(theme_id: str, number: int, home: HomeDep) -> ThemeVersionRecord:
    return home.theme_version(theme_id, number)


@router.post("/api/themes/{theme_id}/reimport", status_code=status.HTTP_202_ACCEPTED)
def reimport_theme(theme_id: str, body: ReimportIn, state: StateDep, wait: Wait = None) -> JobInfo:
    """Новая версия шаблона из файла: роли переносятся, сценарии переходят на неё, если на ней
    нет новых ошибок; остальные остаются на прежней версии (``lost``)."""
    return _import(state, body.path, theme_id, None, body.comment, wait)


@router.put("/api/themes/{theme_id}/roles")
def set_roles(theme_id: str, body: RolesIn, home: HomeDep, state: StateDep) -> ThemeImportOut:
    """Подтвердить роли макетов: новая версия шаблона с тем же файлом."""
    out = _out(home.set_theme_roles(theme_id, body.roles, body.comment))
    for what, ident in _changed(out):
        state.changed(what, ident)
    return out


@router.post("/api/themes/{theme_id}/export")
def export_theme(theme_id: str, body: ExportIn, home: HomeDep) -> dict[str, str]:
    """Сохранить файл шаблона (версии) в выбранную папку или файл."""
    return {"path": str(home.export_theme(theme_id, body.out, body.version))}


@router.delete("/api/themes/{theme_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_theme(theme_id: str, home: HomeDep, state: StateDep) -> None:
    home.delete_theme(theme_id)
    state.changed("themes", theme_id)
