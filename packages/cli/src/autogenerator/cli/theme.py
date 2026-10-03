"""``agen theme``: что приложение видит в шаблоне оформления, заготовка слайдов сценария и
шаблоны в папке данных (версии, роли макетов)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from autogenerator import api
from autogenerator.contracts import AgenError, IssueLevel, ThemeManifest

from .data import HomeOpt, _home
from .library import print_theme_import
from .output import fail

theme_app = typer.Typer(
    help="Шаблон оформления .pptx: проверка, заготовка слайдов, шаблоны в папке данных и роли макетов.",
    no_args_is_help=True,
)


def _load(template: Path, home: Path | None = None) -> tuple[ThemeManifest, str]:
    """Манифест шаблона: из файла или (если файла нет) — шаблона с таким id в папке данных."""
    if not template.exists() and len(template.parts) == 1 and template.suffix == "":
        with _home(home, write=False) as h:
            if h.has_theme(str(template)):
                t = h.theme(str(template))
                return t.current.manifest, f"{t.id} v{t.version}"
    try:
        return api.check_theme(template), template.name
    except AgenError as e:
        fail(e)


@theme_app.command("check")
def check(
    template: Annotated[Path, typer.Argument(help="Шаблон .pptx или .potx, или id шаблона из папки данных")],
    layouts: Annotated[bool, typer.Option("--layouts", help="Показать все макеты с плейсхолдерами")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Все метки и все заметки проверки")] = False,
    json: Annotated[bool, typer.Option("--json", help="Манифест шаблона в JSON")] = False,
    home: HomeOpt = None,
) -> None:
    """Роли макетов, слайды-образцы с метками, графиками и таблицами, шрифты и отчёт проверки
    шаблона. Шаблон не меняется: работа идёт с копией."""
    m, name = _load(template, home)
    if json:
        typer.echo(m.model_dump_json(indent=2))
        return
    typer.echo(api.describe_theme(m, layouts=layouts, verbose=verbose, name=name))
    if any(i.level == IssueLevel.ERROR for i in m.lint):
        raise typer.Exit(1)


@theme_app.command("scaffold")
def scaffold(
    template: Annotated[Path, typer.Argument(help="Шаблон .pptx или .potx, или id шаблона из папки данных")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Записать в файл .yaml")] = None,
    home: HomeOpt = None,
) -> None:
    """Заготовка слайдов сценария: все слайды-образцы шаблона с метками, графиками и
    таблицами, которые осталось привязать."""
    m, name = _load(template, home)
    text = api.scaffold_theme(m, name)
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")
    typer.echo(f"Заготовка записана: {output}")


@theme_app.command("import")
def theme_import(
    file: Annotated[Path, typer.Argument(help="Шаблон .pptx или .potx")],
    id: Annotated[str | None, typer.Option("--id", help="id шаблона; по умолчанию — по имени файла")] = None,
    name: Annotated[str | None, typer.Option(help="Название шаблона")] = None,
    comment: Annotated[str, typer.Option(help="Комментарий к версии")] = "",
    home: HomeOpt = None,
) -> None:
    """Загрузить шаблон в папку данных. Если шаблон с таким id есть, это его новая версия:
    подтверждённые роли макетов переносятся, сценарии на нём получают новую версию."""
    with _home(home, write=True) as h:
        try:
            imp = h.import_theme(file, id, name=name, comment=comment)
        except AgenError as e:
            fail(e)
        print_theme_import(imp)
        if not imp.skipped:
            errors = [i for i in imp.manifest.lint if i.level == IssueLevel.ERROR]
            typer.echo(
                f"Отчёт о шаблоне: agen theme check {imp.record.id}" + (f" (ошибок: {len(errors)})" if errors else "")
            )
        if imp.lost:
            raise typer.Exit(2)


@theme_app.command("list")
def theme_list(home: HomeOpt = None) -> None:
    """Шаблоны в папке данных."""
    with _home(home, write=False) as h:
        items = h.themes()
        if not items:
            typer.echo("Шаблонов нет. Загрузить: agen theme import шаблон.pptx")
            return
        users: dict[str, list[str]] = {}
        for s in h.scenarios():
            if s.current.theme_id:
                users.setdefault(s.current.theme_id, []).append(s.id)
        for t in items:
            who = f"; сценарии: {', '.join(users[t.id])}" if t.id in users else ""
            typer.echo(f"{t.id:<24} v{t.version:<3} {t.name} ({t.current.original_name}){who}")


@theme_app.command("versions")
def theme_versions(theme_id: str, home: HomeOpt = None) -> None:
    """Версии шаблона: файл, когда загружен, подтверждённые роли."""
    with _home(home, write=False) as h:
        try:
            versions = h.theme_versions(theme_id)
        except AgenError as e:
            fail(e)
        for v in versions:
            carried = v.roles and "роли" not in v.comment
            roles = f"; роли: {', '.join(f'{k} → {x}' for k, x in v.roles.items())}" if carried else ""
            typer.echo(
                f"  {v.number:>3}  {v.imported_at.astimezone():%d.%m.%Y %H:%M}  {v.original_name}  {v.comment}{roles}"
            )


@theme_app.command("roles")
def theme_roles(
    theme_id: str,
    assign: Annotated[
        list[str] | None,
        typer.Argument(help="роль=id макета (например title_only=2147483661); роль= — снять подтверждение"),
    ] = None,
    home: HomeOpt = None,
) -> None:
    """Роли макетов шаблона: без аргументов — показать, с аргументами — подтвердить. Каждое
    подтверждение — новая версия шаблона; сценарии на нём получают новую версию."""
    with _home(home, write=bool(assign)) as h:
        try:
            if assign:
                roles: dict[str, str | None] = {}
                for a in assign:
                    if "=" not in a:
                        raise typer.BadParameter(f"«{a}»: нужно роль=id макета", param_hint="ASSIGN")
                    role, key = a.split("=", 1)
                    roles[role.strip()] = key.strip() or None
                imp = h.set_theme_roles(theme_id, roles)
                print_theme_import(imp)
            t = h.theme(theme_id)
        except AgenError as e:
            fail(e)
        names = {lay.key: lay.name for lay in t.current.manifest.layouts}
        typer.echo(f"Роли макетов «{t.id}» v{t.version}:")
        for b in t.current.manifest.roles:
            how = "подтверждена" if not b.guessed else "предложена приложением"
            typer.echo(f"  {b.role.value:<23}→ «{names.get(b.layout_key, b.layout_name)}» (id {b.layout_key}), {how}")
        typer.echo("Макеты: " + "; ".join(f"{k} «{n}»" for k, n in names.items()))


@theme_app.command("export")
def theme_export(
    theme_id: str,
    output: Annotated[Path, typer.Option("--output", "-o", help="Файл или папка")] = Path("."),
    version: Annotated[int | None, typer.Option(help="Версия; по умолчанию — текущая")] = None,
    home: HomeOpt = None,
) -> None:
    """Копия файла шаблона: доработать в PowerPoint и загрузить снова (agen theme import --id)."""
    with _home(home, write=False) as h:
        try:
            out = h.export_theme(theme_id, output, version)
        except AgenError as e:
            fail(e)
        typer.echo(f"Шаблон записан: {out}")


@theme_app.command("delete")
def theme_delete(
    theme_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="Не спрашивать подтверждения")] = False,
    home: HomeOpt = None,
) -> None:
    """Удалить шаблон со всеми версиями (если на нём нет сценариев)."""
    with _home(home, write=True) as h:
        try:
            t = h.theme(theme_id)
            if not yes and not typer.confirm(f"Удалить шаблон «{t.id}» и {t.version} версий?"):
                raise typer.Exit(1)
            h.delete_theme(theme_id)
        except AgenError as e:
            fail(e)
        typer.echo(f"Шаблон «{theme_id}» удалён.")
