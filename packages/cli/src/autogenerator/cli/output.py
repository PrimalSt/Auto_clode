"""Общий вывод команд: ошибки, числа, прогресс, снимок структуры и профиль столбцов."""

from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType
from typing import NoReturn

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TaskProgressColumn, TextColumn, TimeElapsedColumn

from autogenerator.contracts import (
    ColumnProfile,
    IssueLevel,
    RaggedRows,
    ReadOptions,
    ReadProgress,
    RunResult,
    SchemaSnapshot,
)

LEVEL_MARK = {IssueLevel.INFO: "·", IssueLevel.WARNING: "!", IssueLevel.ERROR: "✗"}
RAGGED_HELP = (
    "Строки CSV, где полей больше, чем в шапке: error — ошибка (по умолчанию), truncate — отбросить лишние "
    "поля и показать, у скольких строк"
)

home_option = typer.Option(
    "--home",
    help="Папка данных; по умолчанию — переменная AGEN_HOME или папка приложения "
    "(%LOCALAPPDATA%\\Autogenerator в Windows)",
)


def fail(e: Exception) -> NoReturn:
    typer.echo(f"Ошибка: {e}", err=True)
    raise typer.Exit(1)


def fmt_int(n: int | None) -> str:
    return "—" if n is None else f"{n:,}".replace(",", " ")


def fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} Б"


def read_options_from(
    encoding: str | None,
    delimiter: str | None,
    no_quote: bool,
    header_row: int | None,
    sheet: list[str] | None,
    ragged: RaggedRows | None,
) -> ReadOptions:
    """Параметры чтения из опций команды: заданы только те, что указаны."""
    data: dict[str, object] = {}
    if encoding:
        data["encoding"] = encoding
    if delimiter:
        data["delimiter"] = "\t" if delimiter in ("\\t", "tab") else delimiter
    if no_quote:
        data["quote"] = None
    if header_row:
        data["header_row"] = header_row
    if sheet:
        refs: list[str | int] = [int(s) if s.isdigit() else s for s in sheet]
        data["sheet"] = refs[0] if len(refs) == 1 else refs
    if ragged:
        data["ragged"] = ragged
    return ReadOptions.model_validate(data)


class ProgressView:
    """Прогресс загрузки в консоли: этап, полоса по байтам или листам, число строк.

    Вызывается из потоков чтения и записи; в канал и файл (не в консоль) ничего не пишет.
    """

    def __init__(self, title: str):
        self.console = Console(stderr=True)
        self.progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[rows]}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
            disable=not self.console.is_terminal,
        )
        self.title = title
        self.task: TaskID | None = None

    def __enter__(self) -> ProgressView:
        self.progress.start()
        self.task = self.progress.add_task(self.title, total=None, rows="")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.progress.stop()

    def pause(self) -> None:
        """Остановить вывод перед вопросом пользователю."""
        self.progress.stop()

    def __call__(self, ev: ReadProgress) -> None:
        if self.task is None:
            return
        if ev.unit == "rows":
            self.progress.update(self.task, rows=f"{fmt_int(ev.done)} строк")
            if ev.stage != "запись":
                self.progress.update(self.task, description=f"{self.title}: {ev.stage}", total=None)
            return
        self.progress.update(self.task, description=f"{self.title}: {ev.stage}", total=ev.total, completed=ev.done)


def _short(v: str | None, width: int = 24) -> str:
    """Значение в одну строку: переносы и лишние пробелы схлопываются, длинное обрезается."""
    if v is None:
        return ""
    v = " ".join(v.split())
    return v if len(v) <= width else v[: width - 1] + "…"


def print_profile(profiles: dict[str, ColumnProfile], names: dict[str, str] | None = None) -> None:
    """Профиль столбцов таблицей: пустые, уникальные, минимум и максимум, частые значения."""
    if not profiles:
        return
    width = max(len(names.get(k, k) if names else k) for k in profiles) + 2
    exact = all(p.exact for p in profiles.values())
    typer.echo(f"  Профиль {'по всей загрузке' if exact else 'по выборке'}:")
    typer.echo(f"    {'столбец':<{width}}{'пустых':>9}{'уникальных':>13}  от … до / частые значения")
    for k, p in profiles.items():
        name = names.get(k, k) if names else k
        share = f"{p.nulls / p.rows:.0%}" if p.rows else "—"
        uniq = ("≈" if p.unique_approx else "") + fmt_int(p.unique)
        if p.top:
            rest = ", ".join(f"{_short(t.value, 18)} ({fmt_int(t.count)})" for t in p.top[:3])
        else:
            rest = f"{_short(p.min)} … {_short(p.max)}" if p.min is not None else ""
        typer.echo(f"    {name:<{width}}{share:>9}{uniq:>13}  {rest}")


def print_snapshot(file: Path, snap: SchemaSnapshot, preview: int = 0) -> None:
    o = snap.options
    if o.encoding:
        quote = "без кавычек" if o.quote is None else f"кавычки {o.quote}"
        how = f"кодировка {o.encoding}, разделитель {o.delimiter!r}, {quote}"
    else:
        how = "листы " + ", ".join(f"«{s}»" for s in snap.sheets) if snap.sheets else "лист 1"
    typer.echo(f"{file.name}: {snap.format}, {how}, заголовки в строке {o.header_row}")
    est = f", строк ≈ {fmt_int(snap.rows_estimate)}" if snap.rows_estimate else ""
    parts = ", ".join(snap.sample_parts)
    typer.echo(f"  Размер {fmt_bytes(snap.file_size)}{est}; выборка {fmt_int(snap.sample_rows)} строк ({parts})")
    width = max(len(c.source_name) for c in snap.columns) + 2
    for c in snap.columns:
        fmt = f" ({c.format})" if c.format else ""
        share = f" {c.parsed_share:.1%}" if c.parsed_share is not None and c.parsed_share < 1 else ""
        examples = ", ".join(_short(v, 40) for v in c.sample[:3])
        typer.echo(f"  {c.source_name:<{width}}{c.dtype.value:<9}{fmt + share:<22} например: {examples}")
    profiles = {c.source_name: c.profile for c in snap.columns if c.profile is not None}
    print_profile(profiles)
    for n in snap.notes:
        typer.echo(f"  · {n}")
    if preview and snap.preview:
        typer.echo("  Первые строки:")
        for row in snap.preview[:preview]:
            typer.echo("    " + " | ".join(_short(v, 16) for v in row))


def slides_text(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} слайд"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} слайда"
    return f"{n} слайдов"


def print_result(res: RunResult, verbose: bool) -> None:
    if res.period is not None:
        typer.echo(f"Отчётный период: {res.period.key}")
    for inp, uploads in res.inputs.items():
        parts = ", ".join(f"{Path(u['file']).name} ({u['period']}, {u['rows']} строк)" for u in uploads)
        where = " (история из папки данных)" if inp in res.from_home else ""
        typer.echo(f"Вход {inp}{where}: {parts}")
    for n in res.nodes:
        if verbose or n.state != "ok":
            rows = f" {n.rows_in}→{n.rows_out} строк" if n.rows_in is not None else ""
            typer.echo(f"  {n.state:<7} {n.id}{rows}{' — ' + n.message if n.message else ''}")
    for i in res.issues:
        if i.level == IssueLevel.INFO and not verbose:
            continue
        typer.echo(f"  {LEVEL_MARK[i.level]} {i}")
    if res.output_path:
        typer.echo(f"Готово: {res.output_path} ({slides_text(res.slides)}, {res.seconds:.1f} с)")
    if res.workdir:
        typer.echo(f"Рабочая папка: {res.workdir}")


def utf8_output() -> None:
    """В Windows вывод в канал или файл идёт в кодировке системы, и русский текст ломается.
    В консоли Python и так пишет Юникодом; для канала и файла включаем UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


__all__ = [
    "LEVEL_MARK",
    "RAGGED_HELP",
    "ProgressView",
    "fail",
    "fmt_bytes",
    "fmt_int",
    "home_option",
    "print_profile",
    "print_result",
    "print_snapshot",
    "read_options_from",
    "slides_text",
    "utf8_output",
]
