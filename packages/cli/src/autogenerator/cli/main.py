"""Команда ``agen`` (ARCHITECTURE.md, раздел 6.7).

CLI вызывает движок напрямую через фасад ``api`` и сам пишет метаданные папки данных,
взяв её блокировку. Когда появится сервер приложения (этап M4), при открытом приложении
CLI будет работать через него.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from autogenerator import api
from autogenerator.contracts import AgenError, IssueLevel, PreviewResult, RaggedRows

from .data import history, source_app, upload_app
from .library import scenario_app, stored_scenario
from .output import (
    LEVEL_MARK,
    RAGGED_HELP,
    fail,
    home_option,
    print_result,
    print_snapshot,
    read_options_from,
    utf8_output,
)
from .runs import backup_app, run_stored, runs_app
from .theme import theme_app

app = typer.Typer(
    name="agen",
    help="Autogenerator: отчётные презентации из выгрузок.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

app.add_typer(source_app, name="source")
app.add_typer(upload_app, name="upload")
app.add_typer(theme_app, name="theme")
app.add_typer(scenario_app, name="scenario")
app.add_typer(runs_app, name="runs")
app.add_typer(backup_app, name="backup")
app.command()(history)


def _fail(e: AgenError) -> None:
    fail(e)


def _parse_inputs(values: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in values:
        if "=" not in v:
            raise typer.BadParameter(f"«{v}»: нужно id_входа=путь/к/файлу", param_hint="--input")
        k, path = v.split("=", 1)
        out.setdefault(k.strip(), []).append(path.strip())
    return out


@app.command()
def run(
    scenario: Annotated[Path, typer.Argument(help="Сценарий .yaml или id сценария из папки данных")],
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
    no_home: Annotated[
        bool, typer.Option("--no-home", help="Не брать историю из папки данных, только файлы выгрузок")
    ] = False,
    home: Annotated[Path | None, home_option] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Показать все узлы и замечания")] = False,
) -> None:
    """Собрать отчёт по сценарию.

    История входа — из файлов --input или --data; если их нет — из папки данных (agen upload add),
    когда там есть источник входа; иначе — из папки data рядом со сценарием.

    Сохранённый сценарий (agen scenario add) запускается по id: история — из папки данных,
    отчёт — в папке данных и копией в -o, запуск — в истории запусков (agen runs).
    """
    if stored_scenario(home, scenario):
        if sources or data or input or theme or output_dir or no_home:
            raise typer.BadParameter(
                "у сохранённого сценария источники, история и шаблон — из папки данных; "
                "доступны --period, -o, --workdir, --accept-cast-errors",
                param_hint="SCENARIO",
            )
        run_stored(home, str(scenario), period, output, accept_cast_errors, workdir, verbose)
        return
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
            home=home,
            use_home=False if no_home else None,
        )
    except AgenError as e:
        _fail(e)
        return
    print_result(res, verbose)
    if not res.output_path:
        typer.echo("Отчёт не собран.", err=True)
        raise typer.Exit(1)
    if res.errors:
        typer.echo("Отчёт собран с ошибками: см. пометки «Ошибка» на слайдах.", err=True)
        raise typer.Exit(2)


@app.command()
def validate(
    scenario: Annotated[Path, typer.Argument(help="Сценарий .yaml или id сценария из папки данных")],
    sources: Annotated[Path | None, typer.Option(help="Источники .yaml")] = None,
    theme: Annotated[Path | None, typer.Option(help="Шаблон .pptx")] = None,
    no_home: Annotated[bool, typer.Option("--no-home", help="Не брать источники из папки данных")] = False,
    home: Annotated[Path | None, home_option] = None,
) -> None:
    """Проверить сценарий без данных: ссылки, плагины, параметры, макеты шаблона и
    слайды-образцы. Источники, которых нет в --sources, берутся из папки данных."""
    if stored_scenario(home, scenario):
        from .library import scenario_validate

        scenario_validate(str(scenario), None, home)
        return
    try:
        issues = api.validate(scenario, sources=sources, theme=theme, home=home, use_home=False if no_home else None)
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


def _num(n: int | None, approx: bool) -> str:
    if n is None:
        return "?"
    text = f"{n:,}".replace(",", " ")
    return f"≈{text}" if approx else text


def _sample_option(value: str) -> int | None:
    v = value.strip().lower()
    if v == "auto":
        return None
    if v == "off":
        return 1
    if v.isdigit() and int(v) >= 1:
        return int(v)
    raise typer.BadParameter("auto, off или число не меньше 1", param_hint="--sample")


def _print_preview(res: PreviewResult, rows: int) -> None:
    import polars as pl

    head = f"Превью {res.target}"
    if res.period is not None:
        head += f" · период {res.period.key}"
    if res.sample is not None:
        keys = "; ".join(f"{i}: {', '.join(k) or 'номер строки'}" for i, k in res.sample.keys.items())
        head += f" · выборка ≈1/{res.sample.k} ({keys})"
    typer.echo(head)
    approx_steps = res.sample is not None and res.target.startswith("input:")
    if res.steps:
        typer.echo("Шаги:")
        stop = res.target.split("/step:", 1)[1] if "/step:" in res.target else None
        for st in res.steps:
            if not st.enabled:
                typer.echo(f"  {st.id:<20} ({st.type}) отключён")
                continue
            if st.error:
                typer.echo(f"  {st.id:<20} ({st.type}) ошибка: {st.error}")
                continue
            if st.rows_before is None:
                continue
            mark = "  ← превью после этого шага" if st.id == stop else ""
            counts = f"{_num(st.rows_before, approx_steps)} → {_num(st.rows_after, approx_steps)}"
            typer.echo(f"  {st.id:<20} ({st.type}) {counts}{mark}")
    if res.value is not None or res.metrics:
        for k, v in (res.metrics or {res.target: res.value}).items():
            typer.echo(f"  {k} = {'нет данных' if v is None else v}")
    elif res.columns:
        typer.echo(f"Строк: {_num(res.total_rows, approx_steps)}")
        df = pl.DataFrame(res.rows, schema=[c.name for c in res.columns], orient="row") if res.rows else None
        if df is not None:
            # Дробные числа — с разумной точностью: 2 знака у больших значений, 4 у долей.
            df = df.with_columns(
                pl.col(c).round(2 if (df[c].abs().max() or 0) >= 100 else 4)  # type: ignore[operator]
                for c, t in df.schema.items()
                if t.is_float()
            )
            with pl.Config(
                tbl_rows=rows,
                tbl_cols=-1,
                tbl_width_chars=10_000,
                fmt_str_lengths=40,
                tbl_hide_dataframe_shape=True,
                fmt_float="full",
            ):
                typer.echo(str(df))
    for i in res.issues:
        typer.echo(f"  {LEVEL_MARK[i.level]} {i}")
    typer.echo(f"({res.seconds:.2f} с)")


@app.command()
def preview(
    scenario: Annotated[Path, typer.Argument(help="Сценарий .yaml")],
    target: Annotated[
        str,
        typer.Argument(
            help="Что показать: вход (sales), вход после шага (sales/dedupe), набор (dataset:…), показатель "
            "(metric:…) или слайд (slide:3 — пробная сборка одного слайда)"
        ),
    ],
    sources: Annotated[Path | None, typer.Option(help="Источники .yaml")] = None,
    data: Annotated[Path | None, typer.Option(help="Папка выгрузок: по подпапке на вход")] = None,
    input: Annotated[
        list[str] | None,
        typer.Option("--input", "-i", help="id_входа=файл; можно несколько раз, по порядку загрузки"),
    ] = None,
    period: Annotated[str | None, typer.Option(help="Отчётный период; по умолчанию — последний")] = None,
    rows: Annotated[int, typer.Option(help="Сколько первых строк показать")] = 20,
    sample: Annotated[
        str,
        typer.Option(help="Выборка: auto — на больших данных, off — без выборки, N — каждый N-й ключ"),
    ] = "auto",
    json: Annotated[bool, typer.Option("--json", help="Вывести результат в JSON")] = False,
    workdir: Annotated[Path | None, typer.Option(help="Сохранить промежуточные данные в папку")] = None,
    accept_cast_errors: Annotated[bool, typer.Option(help="Принять загрузки с нераспознанными значениями")] = False,
    no_home: Annotated[bool, typer.Option("--no-home", help="Не брать историю из папки данных")] = False,
    home: Annotated[Path | None, home_option] = None,
    theme: Annotated[Path | None, typer.Option(help="Шаблон .pptx (для slide:N)")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Файл .pptx пробной сборки (slide:N)")] = None,
    image: Annotated[
        Path | None,
        typer.Option(help="Нарисовать слайд в .png (slide:N): PowerPoint, иначе LibreOffice"),
    ] = None,
) -> None:
    """Превью узла сценария: первые строки и число строк до и после каждого шага, набор
    данных или значение показателя. На больших данных превью входа — по выборке.

    slide:N — пробная сборка одного слайда в .pptx (и картинка с --image): непривязанные и
    пустые метки остаются в тексте и подсвечиваются."""
    if target.startswith("slide:"):
        _preview_slide(
            scenario,
            target,
            sources,
            data,
            input,
            period,
            theme,
            output,
            image,
            workdir,
            accept_cast_errors,
            home,
            no_home,
        )
        return
    try:
        res = api.preview(
            scenario,
            target,
            sources=sources,
            inputs=_parse_inputs(input or []),
            data_dir=data,
            period=period,
            rows=rows,
            sample=_sample_option(sample),
            workdir=workdir,
            accept_cast_errors=accept_cast_errors,
            home=home,
            use_home=False if no_home else None,
        )
    except AgenError as e:
        _fail(e)
        return
    if json:
        typer.echo(res.model_dump_json(indent=2))
    else:
        _print_preview(res, rows)
    if res.errors:
        raise typer.Exit(1)


def _preview_slide(
    scenario: Path,
    target: str,
    sources: Path | None,
    data: Path | None,
    input: list[str] | None,
    period: str | None,
    theme: Path | None,
    output: Path | None,
    image: Path | None,
    workdir: Path | None,
    accept_cast_errors: bool,
    home: Path | None,
    no_home: bool,
) -> None:
    number = target.removeprefix("slide:")
    if not number.isdigit() or int(number) < 1:
        raise typer.BadParameter("slide:N — номер слайда сценария с единицы", param_hint="TARGET")
    try:
        res = api.preview_slide(
            scenario,
            int(number),
            sources=sources,
            inputs=_parse_inputs(input or []),
            data_dir=data,
            theme=theme,
            period=period,
            output=output,
            image=image,
            workdir=workdir,
            accept_cast_errors=accept_cast_errors,
            home=home,
            use_home=False if no_home else None,
        )
    except AgenError as e:
        _fail(e)
        return
    print_result(res, verbose=False)
    if res.image_path:
        note = f" ({res.image_note})" if res.image_note else ""
        typer.echo(f"Картинка: {res.image_path}{note}")
    if not res.output_path:
        typer.echo("Слайд не собран.", err=True)
        raise typer.Exit(1)


@app.command()
def inspect(
    file: Annotated[Path, typer.Argument(help="Файл выгрузки: CSV или Excel")],
    format: Annotated[str | None, typer.Option(help="csv, xlsx или xls")] = None,
    encoding: Annotated[str | None, typer.Option(help="utf-8, utf-8-sig, cp1251; по умолчанию — определить")] = None,
    delimiter: Annotated[str | None, typer.Option(help="Разделитель CSV; tab — табуляция")] = None,
    no_quote: Annotated[bool, typer.Option("--no-quote", help="В CSV нет кавычек")] = False,
    header_row: Annotated[int | None, typer.Option(help="Строка заголовков, с единицы; по умолчанию — найти")] = None,
    sheet: Annotated[list[str] | None, typer.Option(help="Лист Excel (имя или номер с нуля); можно несколько")] = None,
    ragged: Annotated[RaggedRows | None, typer.Option(help=RAGGED_HELP)] = None,
    preview: Annotated[int, typer.Option(help="Показать первые N строк")] = 0,
    no_profile: Annotated[bool, typer.Option("--no-profile", help="Без профиля столбцов")] = False,
    json: Annotated[bool, typer.Option("--json", help="Снимок структуры в JSON")] = False,
) -> None:
    """Структура файла выгрузки: как он прочитан, типы и профиль столбцов по выборке."""
    opts = read_options_from(encoding, delimiter, no_quote, header_row, sheet, ragged)
    try:
        snap = api.inspect(file, opts, format, profile=not no_profile)
    except AgenError as e:
        _fail(e)
        return
    if json:
        typer.echo(snap.model_dump_json(indent=2))
        return
    print_snapshot(file, snap, preview)


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
    template: Annotated[
        Path | None,
        typer.Option(
            "--template",
            help="Приёмочный тест на своём шаблоне .pptx (модуль worker): все метки, графики и таблицы "
            "заполняются пробными значениями и проверяются",
        ),
    ] = None,
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
    target, env = str(pkg), None
    if template is not None:
        # Шаблон собирают theme и render вместе, а это делает только worker (ARCHITECTURE.md, раздел 13).
        if pkg.name != "worker":
            typer.echo("--template — приёмочный тест шаблона, он есть только у модуля worker", err=True)
            raise typer.Exit(1)
        if not template.is_file():
            typer.echo(f"Шаблон не найден: {template}", err=True)
            raise typer.Exit(1)
        target = str(pkg / "tests" / "test_template.py")
        env = {**os.environ, "AGEN_TEST_TEMPLATE": str(template.resolve())}
    code = subprocess.call([sys.executable, "-m", "pytest", target, *ctx.args], cwd=root, env=env)
    raise typer.Exit(code)


@app.command()
def version() -> None:
    """Версия приложения и API плагинов."""
    from importlib.metadata import version as v

    from autogenerator.contracts import PLUGIN_API_VERSION

    typer.echo(f"Autogenerator {v('autogenerator-cli')}, API плагинов {PLUGIN_API_VERSION}")


def main() -> None:
    utf8_output()
    app()


if __name__ == "__main__":
    main()
