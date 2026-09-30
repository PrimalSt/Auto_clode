"""Команда ``agen`` (ARCHITECTURE.md, раздел 6.7).

Этап M0: CLI вызывает движок напрямую через фасад ``api``. Когда появится приложение
(этап M4), при открытом приложении CLI будет работать через его сервер.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from autogenerator import api
from autogenerator.contracts import AgenError, IssueLevel, ReadOptions, RunResult

app = typer.Typer(
    name="agen",
    help="Autogenerator: отчётные презентации из выгрузок.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

LEVEL_MARK = {IssueLevel.INFO: "·", IssueLevel.WARNING: "!", IssueLevel.ERROR: "✗"}


def _fail(e: AgenError) -> None:
    typer.echo(f"Ошибка: {e}", err=True)
    raise typer.Exit(1)


def _parse_inputs(values: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in values:
        if "=" not in v:
            raise typer.BadParameter(f"«{v}»: нужно id_входа=путь/к/файлу", param_hint="--input")
        k, path = v.split("=", 1)
        out.setdefault(k.strip(), []).append(path.strip())
    return out


def _print_result(res: RunResult, verbose: bool) -> None:
    if res.period is not None:
        typer.echo(f"Отчётный период: {res.period.key}")
    for inp, uploads in res.inputs.items():
        parts = ", ".join(f"{Path(u['file']).name} ({u['period']}, {u['rows']} строк)" for u in uploads)
        typer.echo(f"Вход {inp}: {parts}")
    for n in res.nodes:
        if verbose or n.state != "ok":
            rows = f" {n.rows_in}→{n.rows_out} строк" if n.rows_in is not None else ""
            typer.echo(f"  {n.state:<7} {n.id}{rows}{' — ' + n.message if n.message else ''}")
    for i in res.issues:
        if i.level == IssueLevel.INFO and not verbose:
            continue
        typer.echo(f"  {LEVEL_MARK[i.level]} {i}")
    if res.output_path:
        typer.echo(f"Готово: {res.output_path} ({res.slides} слайдов, {res.seconds:.1f} с)")
    if res.workdir:
        typer.echo(f"Рабочая папка: {res.workdir}")


@app.command()
def run(
    scenario: Annotated[Path, typer.Argument(help="Сценарий .yaml")],
    sources: Annotated[
        Path | None,
        typer.Option(help="Источники .yaml; по умолчанию sources.yaml рядом со сценарием"),
    ] = None,
    data: Annotated[
        Path | None,
        typer.Option(help="Папка выгрузок: по подпапке на вход; по умолчанию data рядом со сценарием"),
    ] = None,
    input: Annotated[
        list[str] | None,
        typer.Option("--input", "-i", help="id_входа=файл; можно несколько раз, по порядку загрузки"),
    ] = None,
    theme: Annotated[Path | None, typer.Option(help="Шаблон .pptx; по умолчанию theme из сценария")] = None,
    period: Annotated[
        str | None,
        typer.Option(help="Отчётный период: 2026-03, 2026-Q1, 2026; по умолчанию — последний"),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Файл .pptx")] = None,
    output_dir: Annotated[Path | None, typer.Option(help="Папка для отчёта, если -o не задан")] = None,
    workdir: Annotated[
        Path | None,
        typer.Option(help="Сохранить промежуточные данные в папку (для отладки модулей)"),
    ] = None,
    accept_cast_errors: Annotated[bool, typer.Option(help="Принять загрузки с нераспознанными значениями")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Показать все узлы и замечания")] = False,
) -> None:
    """Собрать отчёт по сценарию."""
    try:
        res = api.run(
            scenario,
            sources=sources,
            inputs=_parse_inputs(input or []),
            data_dir=data,
            theme=theme,
            period=period,
            output=output,
            output_dir=output_dir,
            workdir=workdir,
            accept_cast_errors=accept_cast_errors,
        )
    except AgenError as e:
        _fail(e)
        return
    _print_result(res, verbose)
    if not res.output_path:
        typer.echo("Отчёт не собран.", err=True)
        raise typer.Exit(1)
    if res.errors:
        typer.echo("Отчёт собран с ошибками: см. пометки «Ошибка» на слайдах.", err=True)
        raise typer.Exit(2)


@app.command()
def validate(
    scenario: Annotated[Path, typer.Argument(help="Сценарий .yaml")],
    sources: Annotated[Path | None, typer.Option(help="Источники .yaml")] = None,
    theme: Annotated[Path | None, typer.Option(help="Шаблон .pptx")] = None,
) -> None:
    """Проверить сценарий без данных: ссылки, плагины, параметры, макеты шаблона."""
    try:
        issues = api.validate(scenario, sources=sources, theme=theme)
    except AgenError as e:
        _fail(e)
        return
    errors = [i for i in issues if i.level == IssueLevel.ERROR]
    for i in issues:
        typer.echo(f"  {LEVEL_MARK[i.level]} {i}")
    if errors:
        typer.echo(f"Ошибок: {len(errors)}", err=True)
        raise typer.Exit(1)
    typer.echo("Сценарий в порядке.")


@app.command()
def inspect(
    file: Annotated[Path, typer.Argument(help="Файл выгрузки: CSV или Excel")],
    format: Annotated[str | None, typer.Option(help="csv или xlsx")] = None,
    encoding: Annotated[str | None, typer.Option()] = None,
    delimiter: Annotated[str | None, typer.Option()] = None,
    header_row: Annotated[int, typer.Option(help="Строка заголовков, с единицы")] = 1,
    sheet: Annotated[str | None, typer.Option(help="Лист Excel")] = None,
    json: Annotated[bool, typer.Option("--json", help="Снимок структуры в JSON")] = False,
) -> None:
    """Структура файла выгрузки: как он прочитан и какие типы у столбцов."""
    opts = ReadOptions(encoding=encoding, delimiter=delimiter, header_row=header_row, sheet=sheet)
    try:
        snap = api.inspect(file, opts, format)
    except AgenError as e:
        _fail(e)
        return
    if json:
        typer.echo(snap.model_dump_json(indent=2))
        return
    o = snap.options
    how = f"кодировка {o.encoding}, разделитель {o.delimiter!r}" if o.encoding else f"лист {o.sheet!r}"
    typer.echo(
        f"{file.name}: {snap.format}, {how}, заголовки в строке {o.header_row}; выборка {snap.sample_rows} строк"
    )
    width = max(len(c.source_name) for c in snap.columns) + 2
    for c in snap.columns:
        fmt = f" ({c.format})" if c.format else ""
        typer.echo(f"  {c.source_name:<{width}}{c.dtype.value:<9}{fmt:<22} например: {', '.join(c.sample[:3])}")


@app.command()
def modules(json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Модули-плагины и их состояние."""
    manifest = api.modules()
    if json:
        typer.echo(manifest.model_dump_json(indent=2))
        return
    typer.echo(f"API плагинов {manifest.api_version}")
    for p in manifest.plugins:
        mark = "ok" if p.status == "ok" else "ОШИБКА"
        typer.echo(f"  {mark:<7}{p.kind:<12}{p.name:<24}{p.distribution or ''} {p.version or ''}")
        if p.error:
            typer.echo("         " + p.error.strip().splitlines()[-1])
    if manifest.broken():
        raise typer.Exit(1)


def _repo_root() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def test(
    ctx: typer.Context,
    module: Annotated[str, typer.Argument(help="Модуль: engine, ingest, render, …")],
) -> None:
    """Прогнать тесты модуля (режим разработчика: работает в копии исходников)."""
    root = _repo_root()
    if root is None:
        typer.echo("Команда работает в копии исходников (режим разработчика).", err=True)
        raise typer.Exit(1)
    names = {module, module.replace("_", "-"), module.replace("-", "_")}
    pkg = next((root / "packages" / n for n in names if (root / "packages" / n).is_dir()), None)
    if pkg is None:
        known = ", ".join(sorted(p.name for p in (root / "packages").iterdir() if p.is_dir()))
        typer.echo(f"Нет модуля «{module}». Есть: {known}", err=True)
        raise typer.Exit(1)
    code = subprocess.call([sys.executable, "-m", "pytest", str(pkg), *ctx.args], cwd=root)
    raise typer.Exit(code)


@app.command()
def version() -> None:
    """Версия приложения и API плагинов."""
    from importlib.metadata import version as v

    from autogenerator.contracts import PLUGIN_API_VERSION

    typer.echo(f"Autogenerator {v('autogenerator-cli')}, API плагинов {PLUGIN_API_VERSION}")


def _utf8_output() -> None:
    """В Windows вывод в канал или файл идёт в кодировке системы, и русский текст ломается.
    В консоли Python и так пишет Юникодом; для канала и файла включаем UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    _utf8_output()
    app()


if __name__ == "__main__":
    main()
