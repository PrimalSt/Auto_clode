"""``agen runs`` и ``agen backup``: история запусков сохранённых сценариев и резервные копии базы."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from autogenerator.contracts import AgenError, RunRecord, RunStatus

from .data import HomeOpt, _home
from .output import fail, fmt_bytes, print_result

runs_app = typer.Typer(help="История запусков сохранённых сценариев: журнал, отчёт, пересборка.", no_args_is_help=True)
backup_app = typer.Typer(help="Резервные копии базы: источники, сценарии, шаблоны, запуски.", no_args_is_help=True)

STATUS = {
    RunStatus.RUNNING: "идёт",
    RunStatus.OK: "готово",
    RunStatus.ERRORS: "с ошибками",
    RunStatus.FAILED: "не собран",
    RunStatus.INTERRUPTED: "прерван",
}


def print_run(rec: RunRecord, verbose: bool = False) -> None:
    """Журнал запуска (F-608): версии, состав истории, узлы, замечания, где отчёт."""
    period = rec.period.key if rec.period else "?"
    theme = f", шаблон {rec.theme_id} v{rec.theme_version}" if rec.theme_id else ""
    typer.echo(f"Запуск {rec.id}: {rec.scenario_name} v{rec.scenario_version}{theme} — {STATUS[rec.status]}")
    took = f"; {rec.result.seconds:.1f} с" if rec.result else ""
    typer.echo(
        f"  {rec.started_at.astimezone():%d.%m.%Y %H:%M}, период {period}{' (задан)' if rec.period_given else ''}{took}"
    )
    if rec.source_versions:
        typer.echo("  Источники: " + ", ".join(f"{s} v{v}" for s, v in rec.source_versions.items()))
    env = rec.result.environment if rec.result else None
    if env is not None and verbose:
        libs = ", ".join(f"{k} {v}" for k, v in env.libraries.items())
        typer.echo(f"  Приложение {env.app_version}, Python {env.python}; {libs}")
        if env.plugins:
            typer.echo("  Плагины: " + ", ".join(f"{k} {v}" for k, v in env.plugins.items()))
    if rec.result is not None:
        print_result(rec.result.model_copy(update={"output_path": None, "workdir": None}), verbose)
    if rec.output_copy:
        typer.echo(f"Отчёт: {rec.output_copy} (и в папке данных: {rec.output_uri})")
    elif rec.output_uri:
        typer.echo(f"Отчёт: {rec.output_uri}")


def run_stored(
    home: Path | None,
    scenario_id: str,
    period: str | None,
    output: Path | None,
    accept_cast_errors: bool,
    workdir: Path | None,
    verbose: bool,
) -> None:
    """``agen run <id>``: запуск сохранённого сценария с записью в историю запусков."""
    with _home(home, write=True) as h:
        try:
            rec = h.run_scenario(
                scenario_id, period=period, output=output, accept_cast_errors=accept_cast_errors, workdir=workdir
            )
        except AgenError as e:
            fail(e)
        _finish(rec, verbose)


def _finish(rec: RunRecord, verbose: bool) -> None:
    print_run(rec, verbose)
    if rec.status == RunStatus.FAILED:
        typer.echo("Отчёт не собран.", err=True)
        raise typer.Exit(1)
    if rec.status == RunStatus.ERRORS:
        typer.echo("Отчёт собран с ошибками: см. пометки «Ошибка» на слайдах.", err=True)
        raise typer.Exit(2)


@runs_app.command("list")
def runs_list(
    scenario: Annotated[str | None, typer.Option(help="Только запуски этого сценария")] = None,
    limit: Annotated[int, typer.Option(help="Сколько последних показать")] = 20,
    home: HomeOpt = None,
) -> None:
    """Запуски, новые первыми: дата, период, версия сценария, результат."""
    with _home(home, write=False) as h:
        try:
            items = h.runs(scenario, limit)
        except AgenError as e:
            fail(e)
        if not items:
            typer.echo("Запусков нет. Запуск сохранённого сценария: agen run <id>")
            return
        for r in items:
            period = r.period.key if r.period else "?"
            file = r.output_copy or r.output_uri
            out = f"  {Path(file).name}" if file else ""
            typer.echo(
                f"{r.id:<28} {r.started_at.astimezone():%d.%m.%Y %H:%M}  {period:<10} "
                f"v{r.scenario_version:<3} {STATUS[r.status]:<11}{out}"
            )


@runs_app.command("show")
def runs_show(
    run_id: str,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Все узлы, замечания и версии окружения")] = False,
    json: Annotated[bool, typer.Option("--json", help="Журнал запуска в JSON")] = False,
    home: HomeOpt = None,
) -> None:
    """Журнал запуска: версии сценария, источников, шаблона, приложения и плагинов, строки
    после каждого узла, замечания."""
    with _home(home, write=False) as h:
        try:
            rec = h.run_record(run_id)
        except AgenError as e:
            fail(e)
    if json:
        typer.echo(rec.model_dump_json(indent=2))
        return
    print_run(rec, verbose)


@runs_app.command("open")
def runs_open(run_id: str, home: HomeOpt = None) -> None:
    """Открыть отчёт запуска в программе по умолчанию (PowerPoint)."""
    with _home(home, write=False) as h:
        try:
            path = h.run_output(run_id)
        except AgenError as e:
            fail(e)
    typer.echo(str(path))
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.stdout.isatty():
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


@runs_app.command("rerun")
def runs_rerun(
    run_id: str,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Папка или файл для копии отчёта")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    home: HomeOpt = None,
) -> None:
    """Пересобрать отчёт за период прошлого запуска по текущей истории и текущей версии
    сценария (с исправлениями, загруженными позже)."""
    with _home(home, write=True) as h:
        try:
            rec = h.rerun(run_id, output=output)
        except AgenError as e:
            fail(e)
        _finish(rec, verbose)


@runs_app.command("delete")
def runs_delete(run_id: str, home: HomeOpt = None) -> None:
    """Удалить запись запуска и его отчёт в папке данных (копия в выбранной папке остаётся)."""
    with _home(home, write=True) as h:
        try:
            h.delete_run(run_id)
        except AgenError as e:
            fail(e)
        typer.echo(f"Запуск {run_id} удалён.")


# --- резервные копии -------------------------------------------------------------------


@backup_app.command("create")
def backup_create(
    label: Annotated[str, typer.Option(help="Пометка в имени файла (латиница)")] = "manual",
    home: HomeOpt = None,
) -> None:
    """Сделать резервную копию базы. Файлы загрузок и шаблонов не копируются: они не меняются."""
    with _home(home, write=False) as h:
        path = h.backup(label)
    typer.echo(f"Резервная копия: {path}")


@backup_app.command("list")
def backup_list(home: HomeOpt = None) -> None:
    """Резервные копии, новые первыми."""
    with _home(home, write=False) as h:
        items = h.backups()
    if not items:
        typer.echo("Резервных копий нет. Сделать: agen backup create")
        return
    for b in items:
        typer.echo(f"{b.created.astimezone():%d.%m.%Y %H:%M}  {fmt_bytes(b.size):>9}  {b.path.name}")


@backup_app.command("restore")
def backup_restore(
    file: Annotated[Path, typer.Argument(help="Файл резервной копии (или его имя из agen backup list)")],
    yes: Annotated[bool, typer.Option("--yes", help="Не спрашивать подтверждения")] = False,
    home: HomeOpt = None,
) -> None:
    """Вернуть базу из резервной копии. Текущая база перед этим тоже копируется."""
    if not yes and not typer.confirm(f"Вернуть базу из {file.name}? Изменения после неё пропадут из базы."):
        raise typer.Exit(1)
    with _home(home, write=True) as h:
        try:
            safety = h.restore_backup(file)
        except AgenError as e:
            fail(e)
    typer.echo(f"База возвращена из {file.name}. Прежняя база сохранена: {safety}")
