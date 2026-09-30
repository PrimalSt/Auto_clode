"""Публичный фасад: тот же движок из своего кода или Jupyter (F-613).

Пример::

    from autogenerator.api import run
    result = run("examples/sales/scenario.yaml", period="2026-03", output="отчёт.pptx")
    print(result.output_path, result.warnings)

Пути к выгрузкам можно передать явно (``inputs={"sales": ["jan.csv", "feb.csv"]}``) или
положить в папку данных: по подпапке на вход, файлы загружаются в порядке имён.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from autogenerator import worker
from autogenerator.contracts import (
    AgenError,
    ErrorCode,
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
        sources = base / "sources.yaml"
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
) -> RunResult:
    """Собрать отчёт.

    ``inputs`` дополняют и переопределяют файлы из ``data_dir`` (по умолчанию — папка
    ``data`` рядом со сценарием). ``period`` — «2026-03», «2026-Q1» и т. п.; по умолчанию —
    последний период основного входа.
    """
    sc, srcs, theme_path, base = _resolve(scenario, sources, theme)
    files = find_inputs(sc, data_dir if data_dir is not None else base / "data")
    for k, v in (inputs or {}).items():
        files[k] = [str(p) for p in v]
    p = Period.parse(period) if isinstance(period, str) else period
    req = RunRequest(
        scenario=sc,
        sources=srcs,
        inputs=files,
        theme=theme_path,
        period=p,
        output=str(output) if output else None,
        output_dir=str(output_dir) if output_dir else None,
        workdir=str(workdir) if workdir else None,
        accept_cast_errors=accept_cast_errors,
    )
    return worker.run(req)


def inspect(path: str | Path, options: ReadOptions | None = None, fmt: str | None = None) -> SchemaSnapshot:
    """Структура файла выгрузки: столбцы, выведенные типы, примеры значений."""
    return worker.inspect(path, options, fmt)


def modules(isolated: bool = True) -> PluginManifest:
    """Манифест модулей-плагинов с их состоянием."""
    return worker.plugin_manifest(isolated)
