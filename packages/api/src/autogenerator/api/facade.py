"""Публичный фасад: тот же движок из своего кода или Jupyter (F-613).

Пример::

    from autogenerator.api import run
    result = run("examples/sales/scenario.yaml", period="2026-03", output="отчёт.pptx")
    print(result.output_path, result.warnings)

История входа берётся из файлов, переданных явно (``inputs={"sales": ["jan.csv",
"feb.csv"]}`` или папка выгрузок ``data_dir``: по подпапке на вход, в порядке имён), иначе —
из папки данных приложения (``agen upload add``), если там есть его источник, иначе — из
папки ``data`` рядом со сценарием.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from autogenerator import worker
from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    HistoryManifest,
    Issue,
    Period,
    PluginManifest,
    ReadOptions,
    RunRequest,
    RunResult,
    ScenarioSpec,
    SchemaSnapshot,
    SourceSpec,
)
from autogenerator.contracts.yaml_io import load_model_list, load_yaml

DATA_SUFFIXES = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm"}


def load_scenario(path: str | Path) -> ScenarioSpec:
    p = Path(path)
    return worker.load_scenario(load_yaml(p), p.name)


def load_sources(path: str | Path) -> list[SourceSpec]:
    return load_model_list(SourceSpec, path)


def find_inputs(scenario: ScenarioSpec, data_dir: str | Path) -> dict[str, list[str]]:
    """Файлы выгрузок из папки данных: ``<папка>/<id входа>/*`` по порядку имён."""
    root = Path(data_dir)
    out: dict[str, list[str]] = {}
    for inp in scenario.inputs:
        d = root / inp.id
        if d.is_dir():
            files = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in DATA_SUFFIXES)
            out[inp.id] = [str(f) for f in files]
    return out


def _resolve(
    scenario: str | Path | ScenarioSpec,
    sources: str | Path | Sequence[SourceSpec] | None,
    theme: str | Path | None,
) -> tuple[ScenarioSpec, list[SourceSpec], str, Path]:
    if isinstance(scenario, ScenarioSpec):
        sc, base = scenario, Path.cwd()
    else:
        sc, base = load_scenario(scenario), Path(scenario).resolve().parent
    if sources is None:
        default = base / "sources.yaml"
        srcs = load_sources(default) if default.exists() else []
    else:
        srcs = load_sources(sources) if isinstance(sources, str | Path) else list(sources)
    theme_path = Path(theme) if theme else (base / sc.theme if sc.theme else None)
    if theme_path is None or not theme_path.exists():
        raise AgenError(
            ErrorCode.FILE_NOT_FOUND,
            f"Не найден шаблон оформления{f' {theme_path}' if theme_path else ''}",
            hint="Укажите --theme путь/к/шаблону.pptx или поле theme в сценарии (путь относительно сценария).",
        )
    return sc, srcs, str(theme_path), base


def validate(
    scenario: str | Path | ScenarioSpec,
    *,
    sources: str | Path | Sequence[SourceSpec] | None = None,
    theme: str | Path | None = None,
) -> list[Issue]:
    """Проверить сценарий без данных."""
    sc, srcs, theme_path, _ = _resolve(scenario, sources, theme)
    return worker.validate(RunRequest(scenario=sc, sources=srcs, inputs={}, theme=theme_path))


def run(
    scenario: str | Path | ScenarioSpec,
    *,
    sources: str | Path | Sequence[SourceSpec] | None = None,
    inputs: Mapping[str, Sequence[str | Path]] | None = None,
    data_dir: str | Path | None = None,
    theme: str | Path | None = None,
    period: str | Period | None = None,
    output: str | Path | None = None,
    output_dir: str | Path | None = None,
    workdir: str | Path | None = None,
    accept_cast_errors: bool = False,
    home: str | Path | None = None,
    use_home: bool | None = None,
) -> RunResult:
    """Собрать отчёт.

    Для входа без файлов в ``inputs`` и ``data_dir`` история берётся из папки данных
    (``home``, по умолчанию — папка приложения), если там есть его источник, иначе — из
    папки ``data`` рядом со сценарием. ``use_home=False`` — только файлы; ``True`` —
    только папка данных для всех входов без файлов.
    ``period`` — «2026-03», «2026-Q1» и т. п.; по умолчанию — последний период основного
    входа.
    """
    sc, srcs, theme_path, base = _resolve(scenario, sources, theme)
    # Откуда история входа, по убыванию важности: файлы --input, папка выгрузок --data,
    # папка данных приложения, папка data рядом со сценарием.
    files = find_inputs(sc, data_dir) if data_dir is not None else {}
    for k, v in (inputs or {}).items():
        files[k] = [str(p) for p in v]
    histories, home_specs = ({}, []) if use_home is False else _home_histories(sc, files, home, use_home is True)
    if not use_home:
        for k, v in find_inputs(sc, base / "data").items():
            if k not in files and k not in histories:
                files[k] = v
    by_id = {s.id: s for s in srcs}
    for spec in home_specs:
        by_id[spec.id] = spec
    p = Period.parse(period) if isinstance(period, str) else period
    req = RunRequest(
        scenario=sc,
        sources=list(by_id.values()),
        inputs=files,
        histories=histories,
        theme=theme_path,
        period=p,
        output=str(output) if output else None,
        output_dir=str(output_dir) if output_dir else None,
        workdir=str(workdir) if workdir else None,
        accept_cast_errors=accept_cast_errors,
    )
    return worker.run(req)


def _home_histories(
    sc: ScenarioSpec, files: dict[str, list[str]], home: str | Path | None, required: bool
) -> tuple[dict[str, HistoryManifest], list[SourceSpec]]:
    """Истории входов из папки данных: для входов без файлов, чей источник там есть."""
    from .home import Home

    try:
        h = Home.open(home, create=False)
    except AgenError:
        if required:
            raise
        return {}, []
    histories: dict[str, HistoryManifest] = {}
    specs: list[SourceSpec] = []
    with h:
        for inp in sc.inputs:
            if files.get(inp.id) or not h.has_source(inp.source):
                if required and not files.get(inp.id):
                    h.source(inp.source)  # сообщит, каких источников нет
                continue
            histories[inp.id] = h.history(inp.source)
            specs.append(h.source(inp.source).spec)
    return histories, specs


def inspect(
    path: str | Path, options: ReadOptions | None = None, fmt: str | None = None, profile: bool = True
) -> SchemaSnapshot:
    """Структура файла выгрузки: параметры чтения, столбцы, выведенные типы, профиль по выборке."""
    return worker.inspect(path, options, fmt, profile)


def modules(isolated: bool = True) -> PluginManifest:
    """Манифест модулей-плагинов с их состоянием."""
    return worker.plugin_manifest(isolated)
