"""``agen theme``: что приложение видит в шаблоне оформления и заготовка слайдов сценария."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from autogenerator import api
from autogenerator.contracts import AgenError, IssueLevel, ThemeManifest

from .output import fail

theme_app = typer.Typer(help="Шаблон оформления .pptx: проверка и заготовка слайдов.", no_args_is_help=True)


def _load(template: Path) -> ThemeManifest:
    try:
        return api.check_theme(template)
    except AgenError as e:
        fail(e)


@theme_app.command("check")
def check(
    template: Annotated[Path, typer.Argument(help="Шаблон .pptx или .potx")],
    layouts: Annotated[bool, typer.Option("--layouts", help="Показать все макеты с плейсхолдерами")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Все метки и все заметки проверки")] = False,
    json: Annotated[bool, typer.Option("--json", help="Манифест шаблона в JSON")] = False,
) -> None:
    """Роли макетов, слайды-образцы с метками, графиками и таблицами, шрифты и отчёт проверки
    шаблона. Шаблон не меняется: работа идёт с копией."""
    m = _load(template)
    if json:
        typer.echo(m.model_dump_json(indent=2))
        return
    typer.echo(api.describe_theme(m, layouts=layouts, verbose=verbose, name=template.name))
    if any(i.level == IssueLevel.ERROR for i in m.lint):
        raise typer.Exit(1)


@theme_app.command("scaffold")
def scaffold(
    template: Annotated[Path, typer.Argument(help="Шаблон .pptx или .potx")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Записать в файл .yaml")] = None,
) -> None:
    """Заготовка слайдов сценария: все слайды-образцы шаблона с метками, графиками и
    таблицами, которые осталось привязать."""
    m = _load(template)
    text = api.scaffold_theme(m, template.name)
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")
    typer.echo(f"Заготовка записана: {output}")
