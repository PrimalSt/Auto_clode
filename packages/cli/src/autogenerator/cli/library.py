"""``agen scenario``: сценарии в папке данных — сохранение версиями, список, текст, копия."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from autogenerator.api import Home, SavedScenario, ThemeImport
from autogenerator.contracts import AgenError, Issue, IssueLevel

from .data import HomeOpt, _home
from .output import LEVEL_MARK, fail

scenario_app = typer.Typer(
    help="Сценарии в папке данных: каждое сохранение — новая версия; запуск: agen run <id>.", no_args_is_help=True
)


def print_issues(issues: list[Issue], verbose: bool = False) -> None:
    for i in issues:
        if i.level == IssueLevel.INFO and not verbose:
            continue
        typer.echo(f"  {LEVEL_MARK[i.level]} {i}")


def print_theme_import(imp: ThemeImport, what: str = "Шаблон") -> None:
    t = imp.record
    if imp.skipped:
        typer.echo(f"{what} «{t.id}»: тот же файл, что и версия {t.version} — новой версии нет")
        return
    typer.echo(f"{what} «{t.id}» ({t.name}): версия {t.version}")
    for note in imp.manifest.notes:
        typer.echo(f"  ! {note}")
    for sid in imp.scenarios:
        typer.echo(f"  Сценарий «{sid}» получил новую версию с этой версией шаблона")
        for err in imp.lost.get(sid, []):
            typer.echo(f"    ✗ {err}")


def _print_saved(saved: SavedScenario) -> None:
    r = saved.record
    if saved.theme is not None:
        print_theme_import(saved.theme)
    theme = f", шаблон {r.current.theme_id} v{r.current.theme_version}" if r.current.theme_id else ""
    typer.echo(f"Сценарий «{r.id}» ({r.name}): версия {r.version}{theme}")
    print_issues(saved.issues)
    if saved.errors:
        typer.echo(f"Сохранён с ошибками ({len(saved.errors)}): запустить его не получится, пока их не исправить.")
    else:
        typer.echo(f"Запуск: agen run {r.id}")


@scenario_app.command("add")
def scenario_add(
    file: Annotated[Path, typer.Argument(help="Сценарий .yaml")],
    id: Annotated[str | None, typer.Option("--id", help="id сценария; по умолчанию — по его названию")] = None,
    theme: Annotated[
        str | None, typer.Option(help="Шаблон: id из папки данных или файл .pptx; по умолчанию — theme сценария")
    ] = None,
    comment: Annotated[str, typer.Option(help="Комментарий к версии")] = "",
    home: HomeOpt = None,
) -> None:
    """Сохранить сценарий в папку данных: новый — версией 1, существующий — новой версией.
    Если шаблон в сценарии задан файлом, он загружается в папку данных."""
    with _home(home, write=True) as h:
        try:
            saved = h.save_scenario(file, id, theme=theme, comment=comment)
        except AgenError as e:
            fail(e)
        _print_saved(saved)
        if saved.errors:
            raise typer.Exit(1)


@scenario_app.command("list")
def scenario_list(home: HomeOpt = None) -> None:
    """Сценарии в папке данных."""
    with _home(home, write=False) as h:
        items = h.scenarios()
        if not items:
            typer.echo("Сценариев нет. Добавить: agen scenario add сценарий.yaml")
            return
        for s in items:
            last = h.runs(s.id, limit=1)
            run = f"; последний запуск {last[0].id} ({last[0].status.value})" if last else ""
            theme = f", шаблон {s.current.theme_id} v{s.current.theme_version}" if s.current.theme_id else ""
            inputs = ", ".join(f"{i.input_id}←{i.source_id}" for i in s.inputs)
            typer.echo(f"{s.id:<24} v{s.version:<3} {s.name}{theme}; входы: {inputs}{run}")


@scenario_app.command("show")
def scenario_show(
    scenario_id: str,
    version: Annotated[int | None, typer.Option(help="Версия; по умолчанию — текущая")] = None,
    home: HomeOpt = None,
) -> None:
    """Сценарий: версии, шаблон, входы и итог проверки."""
    with _home(home, write=False) as h:
        try:
            s = h.scenario(scenario_id)
            v = h.scenario_version(scenario_id, version)
            versions = h.scenario_versions(scenario_id)
        except AgenError as e:
            fail(e)
        typer.echo(f"{s.name} ({s.id}), версия {v.number} из {s.version}")
        if v.theme_id:
            typer.echo(f"  Шаблон: {v.theme_id} v{v.theme_version}")
        for inp in v.spec.inputs:
            typer.echo(
                f"  Вход {inp.id}: источник {inp.source}{' (основной)' if inp.id == v.spec.main_input.id else ''}"
            )
        typer.echo(f"  Наборов {len(v.spec.datasets)}, показателей {len(v.spec.metrics)}, слайдов {len(v.spec.slides)}")
        typer.echo("Версии:")
        for x in versions:
            typer.echo(f"  {x.number:>3}  {x.created_at.astimezone():%d.%m.%Y %H:%M}  {x.comment}")
        issues = h.validate_scenario(scenario_id, version)
        if issues:
            typer.echo("Проверка:")
            print_issues(issues)


@scenario_app.command("validate")
def scenario_validate(
    scenario_id: str,
    version: Annotated[int | None, typer.Option(help="Версия; по умолчанию — текущая")] = None,
    home: HomeOpt = None,
) -> None:
    """Проверить сохранённый сценарий без данных."""
    with _home(home, write=False) as h:
        try:
            issues = h.validate_scenario(scenario_id, version)
        except AgenError as e:
            fail(e)
    print_issues(issues, verbose=True)
    errors = [i for i in issues if i.level == IssueLevel.ERROR]
    if errors:
        typer.echo(f"Ошибок: {len(errors)}", err=True)
        raise typer.Exit(1)
    typer.echo("Сценарий в порядке.")


@scenario_app.command("export")
def scenario_export(
    scenario_id: str,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Файл .yaml; по умолчанию — на экран")] = None,
    version: Annotated[int | None, typer.Option(help="Версия; по умолчанию — текущая")] = None,
    home: HomeOpt = None,
) -> None:
    """Сценарий текстом YAML — как его сохранили, с комментариями (для правки и git)."""
    with _home(home, write=False) as h:
        try:
            text = h.scenario_text(scenario_id, version)
        except AgenError as e:
            fail(e)
    if output is None:
        typer.echo(text, nl=not text.endswith("\n"))
        return
    output.write_text(text, encoding="utf-8")
    typer.echo(f"Сценарий записан: {output}")


@scenario_app.command("copy")
def scenario_copy(
    scenario_id: str,
    new_id: str,
    name: Annotated[str | None, typer.Option(help="Название копии; по умолчанию «… (копия)»")] = None,
    home: HomeOpt = None,
) -> None:
    """Копия сценария под новым id."""
    with _home(home, write=True) as h:
        try:
            rec = h.copy_scenario(scenario_id, new_id, name)
        except AgenError as e:
            fail(e)
        typer.echo(f"Сценарий «{rec.id}» ({rec.name}) — копия «{scenario_id}»")


@scenario_app.command("delete")
def scenario_delete(
    scenario_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="Не спрашивать подтверждения")] = False,
    home: HomeOpt = None,
) -> None:
    """Удалить сценарий со всеми версиями и запусками (отчёты в папке данных тоже)."""
    with _home(home, write=True) as h:
        try:
            s = h.scenario(scenario_id)
            n = len(h.runs(scenario_id))
        except AgenError as e:
            fail(e)
        if not yes and not typer.confirm(f"Удалить сценарий «{s.id}» ({s.name}), {s.version} версий и {n} запусков?"):
            raise typer.Exit(1)
        h.delete_scenario(scenario_id)
        typer.echo(f"Сценарий «{scenario_id}» удалён.")


def stored_scenario(home: Path | None, ref: Path) -> bool:
    """Аргумент — id сохранённого сценария, а не файл: файла нет, а сценарий с таким id есть."""
    if ref.exists() or ref.suffix.lower() in (".yaml", ".yml") or len(ref.parts) > 1:
        return False
    try:
        with Home.open(home, create=False) as h:
            return h.has_scenario(str(ref))
    except AgenError:
        return False
