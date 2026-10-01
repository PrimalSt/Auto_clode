"""Команды папки данных: источники, загрузки, история (этап M1).

Пока нет приложения, метаданные пишет сама команда ``agen``, взяв блокировку папки данных.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import typer

from autogenerator.api import Home, UploadOutcome
from autogenerator.contracts import (
    AgenError,
    CoverageReport,
    CoverageState,
    DateSpan,
    IngestResult,
    IssueLevel,
    OverlapPolicy,
    Period,
    PeriodFrom,
    PeriodUnit,
    ReadOptions,
    SourceSpec,
    UploadRecord,
    UploadStatus,
)
from autogenerator.contracts.yaml_io import dump_yaml

from .output import LEVEL_MARK, ProgressView, fail, fmt_bytes, fmt_int, home_option, read_options_from

source_app = typer.Typer(help="Источники: откуда приходят выгрузки и как их читать.", no_args_is_help=True)
upload_app = typer.Typer(help="Загрузки: файлы выгрузок в истории источника.", no_args_is_help=True)

STATUS = {
    UploadStatus.ACTIVE: "в истории",
    UploadStatus.NEEDS_REVIEW: "на проверке",
    UploadStatus.EXCLUDED: "исключена",
}
POLICY = {
    OverlapPolicy.REPLACE_PERIOD: "заменить период",
    OverlapPolicy.APPEND: "добавить строки",
    OverlapPolicy.MERGE_DEDUPE: "объединить по ключам",
    OverlapPolicy.REPLACE_ALL: "заменить всё",
    OverlapPolicy.ASK: "спрашивать при пересечении",
}
UNIT = {
    PeriodUnit.DAY: "день",
    PeriodUnit.WEEK: "неделя",
    PeriodUnit.MONTH: "месяц",
    PeriodUnit.QUARTER: "квартал",
    PeriodUnit.YEAR: "год",
    PeriodUnit.RANGE: "произвольный диапазон",
}
MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
CELL = {CoverageState.COVERED: "■", CoverageState.OVERLAP: "▣", CoverageState.GAP: "·"}

HomeOpt = Annotated[Path | None, home_option]


@contextmanager
def _home(home: Path | None, write: bool) -> Iterator[Home]:
    try:
        with Home.open(home, write=write) as h:
            for w in h.folder.warnings:
                typer.echo(f"! {w}", err=True)
            yield h
    except AgenError as e:
        fail(e)


def _parse_period(text: str | None) -> Period | None:
    if text is None:
        return None
    try:
        return Period.parse(text)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint="--period") from None


def _keys(text: str | None) -> list[str] | None:
    return None if text is None else [k.strip() for k in text.split(",") if k.strip()]


# --- источники -------------------------------------------------------------------


def print_source(spec: SourceSpec, version: int | None = None) -> None:
    v = f", версия настроек {version}" if version else ""
    typer.echo(f"{spec.name} ({spec.id}){v}")
    o = spec.options
    how = ", ".join(f"{k} {v!r}" for k, v in o.model_dump(exclude_none=True).items() if not (k == "quote" and v == '"'))
    typer.echo(f"  Формат: {spec.format or 'по файлу'}{'; ' + how if how else ''}")
    where = (
        f"задаётся при загрузке — по имени файла или --period (столбец {spec.period_column})"
        if spec.period_from == PeriodFrom.UPLOAD
        else f"столбец {spec.period_column}"
    )
    typer.echo(
        f"  Период: {where}, тип «{UNIT[spec.period_type]}»; при пересечении — "
        f"{POLICY[spec.overlap_policy]} ({spec.overlap_policy.value})"
    )
    if spec.keys:
        typer.echo(f"  Ключи: {', '.join(spec.keys)}")
    width = max(len(c.id) for c in spec.columns) + 2
    for c in spec.columns:
        fmt = f" ({c.format})" if c.format else ""
        aliases = f"; ещё: {', '.join(c.aliases)}" if c.aliases else ""
        typer.echo(f"    {c.id:<{width}}{c.dtype.value:<9}{c.name}{fmt}{aliases}")


@source_app.command("list")
def source_list(home: HomeOpt = None) -> None:
    """Источники в папке данных."""
    with _home(home, write=False) as h:
        items = h.sources()
        if not items:
            typer.echo("Источников пока нет. Создать: agen source create <id> --from выгрузка.csv")
            return
        for s in items:
            ups = h.uploads(s.id)
            active = [u for u in ups if u.status == UploadStatus.ACTIVE]
            span = f", {active[0].period.key} … {active[-1].period.key}" if active else ""
            typer.echo(f"  {s.id:<20}{s.name} — загрузок {len(ups)}{span}")


@source_app.command("create")
def source_create(
    source_id: Annotated[str, typer.Argument(help="id источника: латиница, цифры, «_»")],
    sample: Annotated[Path, typer.Option("--from", help="Выгрузка, по которой составить источник")],
    name: Annotated[str | None, typer.Option(help="Название; по умолчанию — имя файла")] = None,
    period_column: Annotated[
        str | None, typer.Option(help="Столбец периода — название в файле; по умолчанию первый столбец с датами")
    ] = None,
    period_type: Annotated[PeriodUnit | None, typer.Option(help="Тип периода; по умолчанию — по датам")] = None,
    period_at_upload: Annotated[
        bool,
        typer.Option(
            "--period-at-upload",
            help="Выгрузка — срез без столбца с отчётным месяцем: период берётся из имени файла или --period "
            "при загрузке. По умолчанию так, если ни один столбец дат не укладывается в один месяц",
        ),
    ] = False,
    overlap: Annotated[OverlapPolicy, typer.Option(help="Что делать при пересечении периодов")] = (
        OverlapPolicy.REPLACE_PERIOD
    ),
    keys: Annotated[str | None, typer.Option(help="Ключевые столбцы через запятую (id)")] = None,
    encoding: Annotated[str | None, typer.Option()] = None,
    delimiter: Annotated[str | None, typer.Option()] = None,
    no_quote: Annotated[bool, typer.Option("--no-quote", help="В CSV нет кавычек")] = False,
    header_row: Annotated[int | None, typer.Option(help="Строка заголовков, с единицы")] = None,
    sheet: Annotated[list[str] | None, typer.Option(help="Лист Excel; можно несколько")] = None,
    out: Annotated[Path | None, typer.Option("--yaml", help="Записать черновик в YAML и не сохранять")] = None,
    upload: Annotated[bool, typer.Option("--upload", help="Сразу загрузить этот файл")] = False,
    home: HomeOpt = None,
) -> None:
    """Составить источник по выгрузке: столбцы с id и типами, столбец и тип периода."""
    opts = read_options_from(encoding, delimiter, no_quote, header_row, sheet)
    with _home(home, write=out is None) as h:
        try:
            spec, _snap = h.draft_source(
                sample,
                source_id,
                name,
                period_column,
                period_type,
                opts,
                period_from=PeriodFrom.UPLOAD if period_at_upload else None,
            )
            spec = spec.model_copy(update={"overlap_policy": overlap})
            if keys is not None:
                spec = SourceSpec.model_validate({**spec.model_dump(), "keys": _keys(keys)})
            if out is not None:
                out.write_text(dump_yaml([spec.model_dump(mode="json", exclude_none=True)]), encoding="utf-8")
                print_source(spec)
                typer.echo(f"Черновик записан: {out}. Поправьте и сохраните: agen source import {out}")
                return
            rec = h.create_source(spec)
        except AgenError as e:
            fail(e)
            return
        print_source(rec.spec, rec.version)
        if rec.spec.period_from == PeriodFrom.UPLOAD and not period_at_upload:
            typer.echo(
                "  Ни один столбец дат не укладывается в один месяц, а в имени файла есть месяц: выгрузка "
                "считается срезом на месяц. Если это не так, укажите --period-column «Название столбца»."
            )
        typer.echo(
            f"Источник «{rec.id}» создан. Поправить: agen source export {rec.id} -o {rec.id}.yaml, затем import."
        )
        if upload:
            _upload_files(h, rec.id, [sample], ReadOptions(), None, None, False, False, True)


@source_app.command("show")
def source_show(
    source_id: str,
    versions: Annotated[bool, typer.Option(help="Показать историю версий настроек")] = False,
    home: HomeOpt = None,
) -> None:
    """Настройки источника."""
    with _home(home, write=False) as h:
        try:
            rec = h.source(source_id)
        except AgenError as e:
            fail(e)
            return
        print_source(rec.spec, rec.version)
        if versions:
            for v in h.source_versions(source_id):
                typer.echo(f"  версия {v.number}: {v.created_at.astimezone():%d.%m.%Y %H:%M} {v.comment}")


@source_app.command("export")
def source_export(
    source_id: str,
    out: Annotated[Path | None, typer.Option("--output", "-o", help="Файл .yaml; по умолчанию — на экран")] = None,
    home: HomeOpt = None,
) -> None:
    """Настройки источника в YAML: поправить руками и вернуть через import."""
    with _home(home, write=False) as h:
        try:
            spec = h.source(source_id).spec
        except AgenError as e:
            fail(e)
            return
        text = dump_yaml([spec.model_dump(mode="json", exclude_none=True, exclude={"version"})])
        if out is None:
            typer.echo(text, nl=False)
        else:
            out.write_text(text, encoding="utf-8")
            typer.echo(f"Записано: {out}")


@source_app.command("import")
def source_import(
    file: Annotated[Path, typer.Argument(help="Источники .yaml (как sources.yaml примера)")],
    comment: Annotated[str, typer.Option(help="Комментарий к новой версии")] = "",
    force: Annotated[bool, typer.Option(help="Разрешить убрать столбцы, которые есть в загрузках")] = False,
    home: HomeOpt = None,
) -> None:
    """Создать источники из YAML или сохранить новую версию настроек."""
    with _home(home, write=True) as h:
        try:
            before = {s.id: s.version for s in h.sources()}
            recs = h.import_sources(file, comment, force)
        except AgenError as e:
            fail(e)
            return
        for r in recs:
            was = before.get(r.id)
            what = "создан" if was is None else "без изменений" if was == r.version else f"версия {r.version}"
            typer.echo(f"  {r.id}: {what}")


@source_app.command("set")
def source_set(
    source_id: str,
    name: Annotated[str | None, typer.Option()] = None,
    period_type: Annotated[PeriodUnit | None, typer.Option()] = None,
    overlap: Annotated[OverlapPolicy | None, typer.Option()] = None,
    keys: Annotated[str | None, typer.Option(help="Ключевые столбцы через запятую; пусто — без ключей")] = None,
    comment: Annotated[str, typer.Option()] = "",
    home: HomeOpt = None,
) -> None:
    """Поменять настройки источника (новая версия; загрузки не переписываются)."""
    with _home(home, write=True) as h:
        try:
            spec = h.source(source_id).spec
            data = spec.model_dump()
            for k, v in (("name", name), ("period_type", period_type), ("overlap_policy", overlap)):
                if v is not None:
                    data[k] = v
            if keys is not None:
                data["keys"] = _keys(keys)
            rec = h.update_source(SourceSpec.model_validate(data), comment)
        except (AgenError, ValueError) as e:
            fail(e)
            return
        print_source(rec.spec, rec.version)


@source_app.command("delete")
def source_delete(
    source_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="Не спрашивать подтверждения")] = False,
    home: HomeOpt = None,
) -> None:
    """Удалить источник со всеми загрузками."""
    with _home(home, write=True) as h:
        try:
            n = len(h.uploads(source_id))
            h.source(source_id)
        except AgenError as e:
            fail(e)
            return
        if not yes and not typer.confirm(f"Удалить источник «{source_id}» и {n} загрузок?"):
            raise typer.Exit(1)
        h.delete_source(source_id)
        typer.echo(f"Источник «{source_id}» удалён.")


# --- загрузки --------------------------------------------------------------------


def _choose_policy(res: IngestResult, spec: SourceSpec, view: ProgressView) -> OverlapPolicy | None:
    """Спросить правило пересечения в консоли; без консоли — не выбирать."""
    if not sys.stdin.isatty():
        return None
    view.pause()
    choices = [OverlapPolicy.REPLACE_PERIOD, OverlapPolicy.APPEND, OverlapPolicy.REPLACE_ALL]
    if spec.keys:
        choices.insert(2, OverlapPolicy.MERGE_DEDUPE)
    typer.echo(f"Период {res.period.key} уже загружен. Что сделать с новой загрузкой?")
    for i, c in enumerate(choices, start=1):
        typer.echo(f"  {i} — {POLICY[c]} ({c.value})")
    typer.echo("  0 — отменить загрузку")
    n = typer.prompt("Выбор", type=int, default=1)
    return choices[n - 1] if 1 <= n <= len(choices) else None


def _print_upload(out: UploadOutcome, verbose: bool, spec: SourceSpec, period_given: bool) -> None:
    r = out.record
    res = out.result
    took = f", {res.upload.seconds:.0f} с" if res.upload.seconds >= 1 else ""
    typer.echo(
        f"#{r.seq} {r.original_name}: {fmt_int(r.rows)} строк, период {r.period.key} — {STATUS[r.status]}"
        f" ({fmt_bytes(r.data_bytes)} на диске{took})"
    )
    if res.upload.sheets and len(res.upload.sheets) > 1:
        typer.echo(f"  Листы: {', '.join(res.upload.sheets)}")
    if spec.period_from == PeriodFrom.UPLOAD and not period_given:
        typer.echo(f"  Период — по имени файла. Если не так: agen upload period {r.id} ГГГГ-ММ")
    elif r.period_from_data and r.period_from_data.unit == PeriodUnit.RANGE and r.period == r.period_from_data:
        typer.echo(f"  Период взят по датам в файле. Поправить: agen upload period {r.id} ГГГГ-ММ-ДД..ГГГГ-ММ-ДД")
    if res.overlaps:
        rule = r.overlap_policy or None
        how = f" — {POLICY[rule]}" if rule else ""
        typer.echo(f"  Пересекается с загрузками: {', '.join(res.overlaps)}{how}")
    if res.upload.empty_rows:
        typer.echo(f"  Пустых строк пропущено: {fmt_int(res.upload.empty_rows)}")
    for i in out.issues:
        if i.level == IssueLevel.INFO and not verbose:
            continue
        typer.echo(f"  {LEVEL_MARK[i.level]} {i.message}")
    if r.status == UploadStatus.NEEDS_REVIEW:
        typer.echo("  Загрузка не вошла в историю, пока её не примут:")
        for reason in r.review_reasons:
            typer.echo(f"    {reason}")
        typer.echo(f"  Принять: agen upload accept {r.id}; строки с ошибками: {r.rejects_uri}")


def _upload_files(
    h: Home,
    source_id: str,
    files: list[Path],
    opts: ReadOptions,
    period: Period | None,
    overlap: OverlapPolicy | None,
    accept: bool,
    force: bool,
    profile: bool,
    verbose: bool = False,
    concat: bool = False,
) -> None:
    spec = h.source(source_id).spec
    failed = 0
    groups: list[list[Path]] = [files] if concat else [[f] for f in files]
    for group in groups:
        label = " + ".join(f.name for f in group)
        try:
            with ProgressView(label) as view:
                out = h.upload(
                    source_id,
                    group,
                    options=opts,
                    period=period,
                    overlap_policy=overlap,
                    choose_policy=lambda res: _choose_policy(res, spec, view),
                    accept_cast_errors=accept,
                    force=force,
                    profile=profile,
                    progress=view,
                )
        except AgenError as e:
            typer.echo(f"{label}: ошибка — {e}", err=True)
            failed += 1
            continue
        _print_upload(out, verbose, spec, period is not None)
    if failed:
        raise typer.Exit(1)


@upload_app.command("add")
def upload_add(
    source_id: Annotated[str, typer.Argument(help="id источника")],
    files: Annotated[list[Path], typer.Argument(help="Файлы выгрузок, по порядку загрузки")],
    period: Annotated[
        str | None, typer.Option(help="Период загрузки: 2026-03, 2026-Q1, 2026-03-03..2026-03-19")
    ] = None,
    overlap: Annotated[
        OverlapPolicy | None, typer.Option(help="Правило для этой загрузки, если у источника «ask»")
    ] = None,
    accept_cast_errors: Annotated[
        bool, typer.Option(help="Принять загрузку с нераспознанными значениями (они станут пустыми)")
    ] = False,
    force: Annotated[bool, typer.Option(help="Загрузить файл, даже если он уже загружен")] = False,
    concat: Annotated[
        bool,
        typer.Option(
            "--concat",
            help="Файлы — части одной выгрузки (например, выгрузка за месяц в нескольких файлах): "
            "склеить их по порядку в одну загрузку",
        ),
    ] = False,
    no_profile: Annotated[bool, typer.Option("--no-profile", help="Не считать точный профиль столбцов")] = False,
    encoding: Annotated[str | None, typer.Option()] = None,
    delimiter: Annotated[str | None, typer.Option()] = None,
    no_quote: Annotated[bool, typer.Option("--no-quote")] = False,
    header_row: Annotated[int | None, typer.Option()] = None,
    sheet: Annotated[list[str] | None, typer.Option()] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    home: HomeOpt = None,
) -> None:
    """Загрузить выгрузки в историю источника."""
    opts = read_options_from(encoding, delimiter, no_quote, header_row, sheet)
    with _home(home, write=True) as h:
        try:
            h.source(source_id)
        except AgenError as e:
            fail(e)
        _upload_files(
            h,
            source_id,
            files,
            opts,
            _parse_period(period),
            overlap,
            accept_cast_errors,
            force,
            not no_profile,
            verbose,
            concat,
        )


def _print_uploads(ups: list[UploadRecord]) -> None:
    for u in ups:
        rule = f", {u.overlap_policy.value}" if u.overlap_policy else ""
        typer.echo(
            f"  #{u.seq:<3} {u.period.key:<23} {fmt_int(u.rows):>12} строк  {STATUS[u.status]:<12}"
            f"{u.uploaded_at.astimezone():%d.%m.%Y %H:%M}  {u.original_name}  [{u.id}{rule}]"
        )


@upload_app.command("list")
def upload_list(source_id: str, home: HomeOpt = None) -> None:
    """Загрузки источника по порядку."""
    with _home(home, write=False) as h:
        try:
            ups = h.uploads(source_id)
            h.source(source_id)
        except AgenError as e:
            fail(e)
            return
        if not ups:
            typer.echo(f"Загрузок нет. Загрузить: agen upload add {source_id} выгрузка.csv")
        _print_uploads(ups)


@upload_app.command("show")
def upload_show(
    upload_id: str,
    json: Annotated[bool, typer.Option("--json", help="Запись загрузки в JSON")] = False,
    home: HomeOpt = None,
) -> None:
    """Подробности загрузки: как прочитан файл, ошибки приведения, профиль столбцов."""
    with _home(home, write=False) as h:
        try:
            u = h.upload_record(upload_id)
        except AgenError as e:
            fail(e)
            return
        if json:
            typer.echo(u.model_dump_json(indent=2))
            return
        typer.echo(f"#{u.seq} {u.original_name} ({fmt_bytes(u.size)}, {u.format}) → источник {u.source_id}")
        typer.echo(
            f"  Период: {u.period.key}" + (f" (по датам: {u.period_from_data.key})" if u.period_from_data else "")
        )
        typer.echo(
            f"  Строк: {fmt_int(u.rows)}; без даты: {fmt_int(u.null_period_rows)}; "
            f"вне периода: {fmt_int(u.rows_outside_period)}"
        )
        typer.echo(f"  Статус: {STATUS[u.status]}; версия настроек источника {u.source_version}")
        if u.options:
            typer.echo(f"  Прочитан с параметрами: {u.options.model_dump(exclude_none=True)}")
        for ci in u.cast_report:
            typer.echo(
                f"  ! {ci.column} ({ci.dtype}): {ci.errors} не распознано, например {', '.join(ci.examples[:5])}"
            )
        if u.profile:
            from .output import print_profile

            print_profile({cid: p for cid, p in u.profile.items()})


def _set_status(upload_id: str, status: UploadStatus, home: Path | None) -> None:
    with _home(home, write=True) as h:
        try:
            u = h.set_upload_status(upload_id, status)
        except AgenError as e:
            fail(e)
            return
        typer.echo(f"#{u.seq} {u.original_name}: {STATUS[u.status]}")


@upload_app.command("exclude")
def upload_exclude(upload_id: str, home: HomeOpt = None) -> None:
    """Исключить загрузку из истории (файлы остаются)."""
    _set_status(upload_id, UploadStatus.EXCLUDED, home)


@upload_app.command("include")
def upload_include(upload_id: str, home: HomeOpt = None) -> None:
    """Вернуть исключённую загрузку в историю."""
    _set_status(upload_id, UploadStatus.ACTIVE, home)


@upload_app.command("accept")
def upload_accept(upload_id: str, home: HomeOpt = None) -> None:
    """Принять загрузку «на проверке»: нераспознанные значения останутся пустыми."""
    _set_status(upload_id, UploadStatus.ACTIVE, home)


@upload_app.command("period")
def upload_period_cmd(upload_id: str, period: str, home: HomeOpt = None) -> None:
    """Поправить период загрузки (например, подтвердить произвольный диапазон)."""
    p = _parse_period(period)
    assert p is not None
    with _home(home, write=True) as h:
        try:
            u = h.set_upload_period(upload_id, p)
        except AgenError as e:
            fail(e)
            return
        extra = f"; строк вне периода: {fmt_int(u.rows_outside_period)}" if u.rows_outside_period else ""
        typer.echo(f"#{u.seq} {u.original_name}: период {u.period.key}{extra}")


@upload_app.command("delete")
def upload_delete(
    upload_id: str,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    home: HomeOpt = None,
) -> None:
    """Удалить загрузку вместе с файлами."""
    with _home(home, write=True) as h:
        try:
            u = h.upload_record(upload_id)
        except AgenError as e:
            fail(e)
            return
        if not yes and not typer.confirm(f"Удалить загрузку #{u.seq} {u.original_name} ({u.period.key})?"):
            raise typer.Exit(1)
        h.delete_upload(upload_id)
        typer.echo(f"Загрузка #{u.seq} удалена.")


# --- история -----------------------------------------------------------------------


def print_coverage(rep: CoverageReport) -> None:
    if not rep.cells:
        return
    if rep.unit == PeriodUnit.MONTH:
        typer.echo("  Покрытие по месяцам (■ есть, ▣ наложение, · пропуск):")
        years: dict[int, list[str]] = {}
        for c in rep.cells:
            row = years.setdefault(c.period.start.year, ["   "] * 12)
            row[c.period.start.month - 1] = CELL[c.state]
        typer.echo("        " + " ".join(f"{m:<3}" for m in MONTHS))
        for y, row in years.items():
            typer.echo((f"    {y} " + " ".join(f"{x:<3}" for x in row)).rstrip())
    else:
        unit = UNIT[rep.unit]
        typer.echo(f"  Покрытие (единица — {unit}; ■ есть, ▣ наложение, · пропуск):")
        line = "    " + " ".join(f"{c.period.key} {CELL[c.state]}" for c in rep.cells[:60])
        typer.echo(line + (" …" if len(rep.cells) > 60 else ""))
    if rep.gaps:
        typer.echo("  Пропуски: " + ", ".join(_span_text(g, rep.unit) for g in rep.gaps))
    if rep.overlaps:
        typer.echo("  Наложения: " + ", ".join(_span_text(g, rep.unit) for g in rep.overlaps))


def _span_text(span: DateSpan, unit: PeriodUnit) -> str:
    """Отрезок по-человечески: «2026-02 … 2026-03», если он из целых единиц шкалы,
    иначе первый и последний день."""
    assert span.start is not None
    last = span.end_exclusive - timedelta(days=1)
    if unit in (PeriodUnit.MONTH, PeriodUnit.QUARTER, PeriodUnit.YEAR):
        a, b = Period.containing(span.start, unit), Period.containing(last, unit)
        if a.start == span.start and b.end_exclusive == span.end_exclusive:
            return a.key if a == b else f"{a.key} … {b.key}"
    return span.start.isoformat() if span.start == last else f"{span.start} … {last}"


def history(
    source_id: Annotated[str, typer.Argument(help="id источника")],
    export: Annotated[Path | None, typer.Option(help="Записать действующую историю в .parquet")] = None,
    json: Annotated[bool, typer.Option("--json", help="Манифест и покрытие в JSON")] = False,
    home: HomeOpt = None,
) -> None:
    """История источника: загрузки, покрытие периодов, пропуски и наложения."""
    with _home(home, write=False) as h:
        try:
            rec = h.source(source_id)
            manifest = h.history(source_id)
            rep = h.coverage(source_id)
        except AgenError as e:
            fail(e)
            return
        if json:
            import json as _json

            typer.echo(
                _json.dumps(
                    {"manifest": manifest.model_dump(mode="json"), "coverage": rep.model_dump(mode="json")},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        spec = rec.spec
        typer.echo(
            f"{spec.name} ({spec.id}): тип периода «{UNIT[spec.period_type]}», при пересечении — "
            f"{POLICY[spec.overlap_policy]}; версия настроек {rec.version}"
        )
        ups = h.uploads(source_id)
        if not ups:
            typer.echo(f"  Загрузок нет. Загрузить: agen upload add {source_id} выгрузка.csv")
            return
        _print_uploads(ups)
        print_coverage(rep)
        if manifest.active_uploads:
            typer.echo(f"  Отчётный период по умолчанию: {h.default_period(source_id).key}")
        typer.echo(f"  Место на диске: {fmt_bytes(h.disk_usage(source_id))}")
        if export is not None:
            try:
                n = h.export_history(source_id, export)
            except AgenError as e:
                fail(e)
                return
            typer.echo(f"  Действующая история ({fmt_int(n)} строк): {export}")
