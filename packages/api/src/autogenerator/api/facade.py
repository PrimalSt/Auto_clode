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
from dataclasses import dataclass, field
from pathlib import Path

from autogenerator import worker
from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    HistoryManifest,
    Issue,
    Period,
    PluginManifest,
    PreviewRequest,
    PreviewResult,
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
    found = _inputs(sc, srcs, base, inputs, data_dir, home, use_home)
    req = RunRequest(
        scenario=sc,
        sources=found.sources,
        inputs=found.files,
        histories=found.histories,
        theme=theme_path,
        period=Period.parse(period) if isinstance(period, str) else period,
        output=str(output) if output else None,
        output_dir=str(output_dir) if output_dir else None,
        workdir=str(workdir) if workdir else None,
        accept_cast_errors=accept_cast_errors,
        cache_dir=found.cache_dir,
        temp_dir=found.temp_dir,
    )
    return worker.run(req)


@dataclass
class _Inputs:
    files: dict[str, list[str]] = field(default_factory=dict)
    histories: dict[str, HistoryManifest] = field(default_factory=dict)
    sources: list[SourceSpec] = field(default_factory=list)
    cache_dir: str | None = None
    temp_dir: str | None = None


def _inputs(
    sc: ScenarioSpec,
    srcs: list[SourceSpec],
    base: Path,
    inputs: Mapping[str, Sequence[str | Path]] | None,
    data_dir: str | Path | None,
    home: str | Path | None,
    use_home: bool | None,
) -> _Inputs:
    """Откуда история входа, по убыванию важности: файлы ``inputs``, папка выгрузок
    ``data_dir``, папка данных приложения, папка ``data`` рядом со сценарием."""
    out = _Inputs()
    out.files = find_inputs(sc, data_dir) if data_dir is not None else {}
    for k, v in (inputs or {}).items():
        out.files[k] = [str(p) for p in v]
    home_specs: list[SourceSpec] = []
    if use_home is not False:
        home_specs = _home_histories(sc, out, home, use_home is True)
    if not use_home:
        for k, v in find_inputs(sc, base / "data").items():
            if k not in out.files and k not in out.histories:
                out.files[k] = v
    by_id = {s.id: s for s in srcs}
    for spec in home_specs:
        by_id[spec.id] = spec
    out.sources = list(by_id.values())
    return out


def _home_histories(sc: ScenarioSpec, out: _Inputs, home: str | Path | None, required: bool) -> list[SourceSpec]:
    """Истории входов из папки данных: для входов без файлов, чей источник там есть. Кэш
    узлов и временные файлы движка тогда тоже в папке данных."""
    from .home import Home

    try:
        h = Home.open(home, create=False)
    except AgenError:
        if required:
            raise
        return []
    specs: list[SourceSpec] = []
    with h:
        for inp in sc.inputs:
            if out.files.get(inp.id) or not h.has_source(inp.source):
                if required and not out.files.get(inp.id):
                    h.source(inp.source)  # сообщит, каких источников нет
                continue
            out.histories[inp.id] = h.history(inp.source)
            specs.append(h.source(inp.source).spec)
        if out.histories:
            out.cache_dir = str(h.folder.root / "cache" / "engine")
            out.temp_dir = str(h.folder.tmp / "engine")
    return specs


def preview(
    scenario: str | Path | ScenarioSpec,
    target: str,
    *,
    sources: str | Path | Sequence[SourceSpec] | None = None,
    inputs: Mapping[str, Sequence[str | Path]] | None = None,
    data_dir: str | Path | None = None,
    period: str | Period | None = None,
    rows: int = 20,
    sample: int | None = None,
    workdir: str | Path | None = None,
    accept_cast_errors: bool = False,
    home: str | Path | None = None,
    use_home: bool | None = None,
) -> PreviewResult:
    """Превью узла сценария: входа после шага (``sales/dedupe``), набора или показателя.

    ``sample``: ``None`` — на больших данных превью входа строится по выборке, ``1`` — без
    выборки, ``k`` — примерно каждая k-я строка (ключ). История — как у ``run``.
    """
    if isinstance(scenario, ScenarioSpec):
        sc, base = scenario, Path.cwd()
    else:
        sc, base = load_scenario(scenario), Path(scenario).resolve().parent
    if sources is None:
        default = base / "sources.yaml"
        srcs = load_sources(default) if default.exists() else []
    else:
        srcs = load_sources(sources) if isinstance(sources, str | Path) else list(sources)
    found = _inputs(sc, srcs, base, inputs, data_dir, home, use_home)
    req = PreviewRequest(
        scenario=sc,
        sources=found.sources,
        inputs=found.files,
        histories=found.histories,
        target=target,
        period=Period.parse(period) if isinstance(period, str) else period,
        rows=rows,
        sample=sample,
        workdir=str(workdir) if workdir else None,
        accept_cast_errors=accept_cast_errors,
        cache_dir=found.cache_dir,
        temp_dir=found.temp_dir,
    )
    return worker.preview(req)


def inspect(
    path: str | Path, options: ReadOptions | None = None, fmt: str | None = None, profile: bool = True
) -> SchemaSnapshot:
    """Структура файла выгрузки: параметры чтения, столбцы, выведенные типы, профиль по выборке."""
    return worker.inspect(path, options, fmt, profile)


def modules(isolated: bool = True) -> PluginManifest:
    """Манифест модулей-плагинов с их состоянием."""
    return worker.plugin_manifest(isolated)
