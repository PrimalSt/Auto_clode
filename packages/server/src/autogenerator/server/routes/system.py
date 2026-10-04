"""Система и модули: состояние сервера, резервные копии, манифест модулей, кэш."""

from __future__ import annotations

import os
import shutil
import threading
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from fastapi import APIRouter, status

from autogenerator.contracts import AgenError, ErrorCode

from ..deps import HomeDep, StateDep
from ..models import BackupIn, BackupOut, ModulesOut, RestoreIn, SystemOut
from ..state import ServerState

router = APIRouter(tags=["система"])


def app_version() -> str:
    try:
        return package_version("autogenerator-server")
    except PackageNotFoundError:
        return "dev"


def _idle(state: ServerState, what: str) -> None:
    if state.busy():
        raise AgenError(
            ErrorCode.IN_USE, f"Идут задания: {what} — после их окончания", hint="Дождитесь заданий или отмените их."
        )


@router.get("/api/health")
def health() -> dict[str, object]:
    """Сервер отвечает (без токена: так оболочка ждёт готовности)."""
    return {"ok": True, "app": "autogenerator"}


@router.get("/api/system")
def system(state: StateDep) -> SystemOut:
    folder = state.home.folder
    return SystemOut(
        version=app_version(),
        pid=os.getpid(),
        home=str(folder.root),
        started_at=state.started_at,
        disk_free=folder.free_bytes(),
        warnings=folder.warnings,
        jobs_active=sum(1 for j in state.jobs.list() if not j.status.finished),
        executors=[ex.info() for ex in state.executors.values()],
    )


@router.post("/api/system/backup", status_code=status.HTTP_201_CREATED)
def backup(body: BackupIn, home: HomeDep) -> BackupOut:
    """Резервная копия базы (файлы загрузок и шаблонов не копируются: они неизменяемые)."""
    p = home.backup(body.label)
    st = p.stat()
    return BackupOut(path=str(p), name=p.name, size=st.st_size, created=datetime.fromtimestamp(st.st_mtime, UTC))


@router.get("/api/system/backups")
def backups(home: HomeDep) -> list[BackupOut]:
    return [BackupOut(path=str(b.path), name=b.path.name, size=b.size, created=b.created) for b in home.backups()]


@router.post("/api/system/restore")
def restore(body: RestoreIn, state: StateDep) -> dict[str, str]:
    """Вернуть базу из резервной копии; текущая база перед этим копируется (путь — ``safety``)."""
    _idle(state, "восстановление базы")
    with state.gate.exclusive():
        safety = state.home.restore_backup(body.path)
    state.changed("all")
    return {"safety": str(safety)}


@router.post("/api/system/shutdown", status_code=status.HTTP_202_ACCEPTED)
def shutdown(state: StateDep) -> dict[str, bool]:
    """Остановить сервер: оболочка вызывает это при закрытии окна (идущие задания прерываются)."""
    if state.shutdown is None:
        raise AgenError(ErrorCode.NOT_IMPLEMENTED, "Этот сервер останавливается тем, кто его запустил")
    threading.Timer(0.2, state.shutdown).start()  # ответить, потом остановиться
    return {"ok": True}


# --- модули ----------------------------------------------------------------------


@router.get("/api/modules")
def modules(state: StateDep) -> ModulesOut:
    """Исполнители и манифест модулей и плагинов (раздел «Модули»). Если исполнитель превью
    не запустился, манифеста нет, а причина — в ``error`` и ``executors[].error``."""
    try:
        manifest = state.home.worker.plugin_manifest()
        error = None
    except AgenError as e:
        manifest, error = None, {"code": str(e.code), "message": e.message, "hint": e.hint}
    return ModulesOut(executors=[ex.info() for ex in state.executors.values()], plugins=manifest, error=error)


@router.post("/api/modules/restart")
def restart_executors(state: StateDep) -> ModulesOut:
    """Перезапустить исполнители (режим разработчика: подхватить изменённый код модулей)."""
    _idle(state, "перезапуск исполнителей")
    for ex in state.executors.values():
        ex.close()
    return modules(state)


@router.post("/api/modules/cache/clear")
def clear_cache(state: StateDep) -> dict[str, int]:
    """Очистить кэш вычислений (узлы пересчитаются при следующем превью или запуске)."""
    _idle(state, "очистка кэша")
    cache = state.home.folder.root / "cache"
    freed = 0
    with state.gate.exclusive():
        for p in cache.iterdir() if cache.is_dir() else []:
            freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    return {"freed": freed}
