"""Превью узла сценария (ARCHITECTURE.md, раздел 6.4, «Превью»): первые строки входа после
любого шага, число строк до и после каждого шага, набор данных или значение показателя.

На больших данных превью входа строится по выборке по хешу ключа: строки с
``hash(ключ) % K = 0`` по всей истории входа в пределах нужных окон. Ключ выбирается так,
чтобы дубликаты и пары ключей объединения попадали в выборку вместе. Выборка кэшируется:
повторное превью после правки шага читает уже её. Наборы и показатели по умолчанию
считаются точно — только по нужным столбцам и окнам.
"""

from __future__ import annotations

import math
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from autogenerator.contracts import (
    AgenError,
    ErrorCode,
    HistoryProvider,
    IssueLevel,
    Period,
    PreviewColumn,
    PreviewResult,
    SampleInfo,
)
from autogenerator.plugin_host import PluginRegistry

from .analysis import ScenarioPlan
from .cache import make_key
from .execute import EngineOptions, SamplePlan, _Engine

SAMPLE_FROM_ROWS = 500_000
"""Вход больше этого числа строк превью берёт выборкой."""
SAMPLE_TARGET_ROWS = 200_000
"""Сколько строк примерно остаётся в выборке самого большого входа."""
PREVIEW_CODE_TIMEOUT = 60.0


def resolve_target(plan: ScenarioPlan, text: str) -> str:
    """Узел превью по записи пользователя: ``sales``, ``sales/dedupe``, ``input:sales/step:dedupe``,
    ``by_month``, ``dataset:by_month``, ``revenue``, ``metric:revenue_prev``."""
    t = text.strip()
    sc = plan.scenario
    inputs = [i.id for i in sc.inputs]
    datasets = [d.id for d in sc.datasets]
    metrics = list(plan.metrics)
    if "/" in t:
        left, right = t.split("/", 1)
        iid = left.removeprefix("input:")
        if iid not in inputs:
            raise AgenError(ErrorCode.NOT_FOUND, f"Нет входа «{iid}». Есть: {', '.join(inputs)}")
        step = right.removeprefix("step:")
        spec = next((s for s in sc.input(iid).pipeline if s.id == step), None)
        if spec is None:
            known = ", ".join(s.id for s in sc.input(iid).pipeline) or "нет шагов"
            raise AgenError(ErrorCode.NOT_FOUND, f"Во входе «{iid}» нет шага «{step}». Есть: {known}")
        if not spec.enabled:
            raise AgenError(ErrorCode.SPEC_INVALID, f"Шаг «{step}» отключён: превью после него — то же, что до него")
        return f"input:{iid}/step:{step}"
    kinds = {"input": inputs, "dataset": datasets, "metric": metrics}
    if ":" in t:
        kind, nid = t.split(":", 1)
        if kind not in kinds:
            raise AgenError(ErrorCode.NOT_FOUND, f"Не понял узел «{t}»: ожидалось input:…, dataset:… или metric:…")
        if nid not in kinds[kind]:
            known = ", ".join(kinds[kind]) or "нет"
            raise AgenError(ErrorCode.NOT_FOUND, f"Нет узла «{t}». Есть ({kind}): {known}")
        return t
    found = [f"{k}:{t}" for k, ids in kinds.items() if t in ids]
    if not found:
        raise AgenError(
            ErrorCode.NOT_FOUND,
            f"В сценарии нет входа, набора или показателя «{t}»",
            hint="Входы: "
            + ", ".join(inputs)
            + "; наборы: "
            + (", ".join(datasets) or "нет")
            + "; показатели: "
            + (", ".join(metrics) or "нет"),
        )
    if len(found) > 1:
        raise AgenError(ErrorCode.SPEC_INVALID, f"«{t}» — это {' и '.join(found)}: уточните, например {found[0]}")
    return found[0]


def sample_keys(plan: ScenarioPlan, iid: str, sampled: set[str]) -> list[str]:
    """Ключ выборки входа: ключ объединения с другим входом из выборки, иначе ключ
    дубликатов, ключ объединения, ключ источника; пусто — номер строки."""
    ip = plan.inputs[iid]
    source = set(ip.schema.columns)

    def usable(cols: list[str]) -> bool:
        return bool(cols) and all(c in source for c in cols)

    def expand(cols: list[str]) -> list[str]:
        # «*» — все столбцы (дубликаты по всем столбцам).
        return sorted(source) if cols == ["*"] else cols

    joins_sampled: list[list[str]] = []
    joins_other: list[list[str]] = []
    dedupe: list[list[str]] = []
    for sp in ip.steps:
        own = expand(sp.keys.get("data", []))
        partners = [k for k in sp.keys if k != "data"]
        if partners:
            (joins_sampled if any(p in sampled for p in partners) else joins_other).append(own)
        elif own:
            dedupe.append(own)
    for other_id, other in plan.inputs.items():
        if other_id == iid:
            continue
        for sp in other.steps:
            if iid in sp.keys:
                (joins_sampled if other_id in sampled else joins_other).append(sp.keys[iid])
    for group in (joins_sampled, dedupe, joins_other, [ip.schema.keys]):
        for cols in group:
            if usable(cols):
                return list(cols)
    return []


def _json_value(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _rows(df: pl.DataFrame) -> tuple[list[PreviewColumn], list[dict[str, Any]]]:
    cols = [c for c in df.columns if not c.startswith("_")]
    df = df.select(cols)
    columns = [PreviewColumn(name=c, dtype=str(t)) for c, t in df.schema.items()]
    rows = [{k: _json_value(v) for k, v in r.items()} for r in df.to_dicts()]
    return columns, rows


def preview(
    plan: ScenarioPlan,
    registry: PluginRegistry,
    history: HistoryProvider,
    period: Period,
    workdir: str | Path,
    target: str,
    *,
    rows: int = 20,
    sample: int | None = None,
    options: EngineOptions | None = None,
) -> PreviewResult:
    """Превью узла ``target`` за отчётный период ``period``.

    ``sample``: ``None`` — выборка для входов, если они большие, а наборы и показатели точно;
    ``1`` — без выборки; ``k > 1`` — выборка примерно каждой k-й строки (ключа) всех входов.
    """
    t0 = time.perf_counter()
    node = resolve_target(plan, target)
    base = node.split("/", 1)[0]
    nodes = [base]
    if base.startswith("metric:") and plan.metrics[base.split(":", 1)[1]].base is None:
        # Показатель — вместе с его сравнениями периодов.
        nodes += [f"metric:{c}" for c in plan.metrics[base.split(":", 1)[1]].spec.compare_ids()]
    closure = plan.closure(nodes)
    result = PreviewResult(target=node, period=period)
    errors = [
        i for i in plan.issues if i.level == IssueLevel.ERROR and (i.node is None or i.node.split("/")[0] in closure)
    ]
    if errors:
        result.issues = errors
        result.seconds = round(time.perf_counter() - t0, 3)
        return result
    opts = options or EngineOptions()
    opts.preview = True
    opts.count_steps = True
    opts.nodes = nodes
    opts.stop_after = node if "/" in node else None
    if opts.code_timeout is None or opts.code_timeout > PREVIEW_CODE_TIMEOUT:
        opts.code_timeout = PREVIEW_CODE_TIMEOUT
    engine = _Engine(plan, registry, history, period, Path(workdir), opts)
    plan_sample = choose_sample(engine, closure, base, sample)
    opts.sample = plan_sample
    res = engine.run()
    result.nodes = res.nodes
    result.issues = [i for i in plan.issues if i.node is None or i.node.split("/")[0] in closure] + res.issues
    k = plan_sample.k if plan_sample else 1
    if plan_sample:
        result.sample = SampleInfo(
            k=k,
            keys={i: plan_sample.keys.get(i, []) for i in sorted(plan_sample.inputs)},
            inputs=sorted(plan_sample.inputs),
        )
        result.approximate = True
    kind, nid = base.split(":", 1)
    if kind == "input":
        steps = res.steps.get(nid, [])
        if plan_sample and nid in plan_sample.inputs:
            for st in steps:
                st.rows_before = st.rows_before * k if st.rows_before is not None else None
                st.rows_after = st.rows_after * k if st.rows_after is not None else None
        result.steps = steps
        path = engine.input_paths.get(nid)
        status = engine.status.get(base)
        if path is not None:
            result.columns, result.rows = _rows(pl.scan_parquet(path).head(rows).collect())
            total = status.rows_out if status else None
            result.total_rows = total * k if total is not None and plan_sample and nid in plan_sample.inputs else total
    elif kind == "dataset":
        df = engine.frames.get(nid)
        if df is not None:
            result.columns, result.rows = _rows(df.head(rows))
            result.total_rows = df.height
    else:
        result.value = res.metrics.get(nid)
        mp = plan.metrics[nid]
        ids = [nid] + ([] if mp.base is not None else mp.spec.compare_ids())
        result.metrics = {m: res.metrics.get(m) for m in ids if m in res.metrics or m == nid}
    result.seconds = round(time.perf_counter() - t0, 3)
    return result


def choose_sample(engine: _Engine, closure: set[str], base: str, sample: int | None) -> SamplePlan | None:
    """Решить, какие входы брать выборкой и с каким K."""
    if sample == 1:
        return None
    if sample is None and not base.startswith("input:"):
        return None
    plan = engine.plan
    counts: dict[str, int] = {}
    for n in sorted(closure):
        if not n.startswith("input:"):
            continue
        iid = n.split(":", 1)[1]
        ip = plan.inputs.get(iid)
        if ip is None:
            continue
        try:
            counts[iid] = history_rows(engine, iid, engine.lower_bound(ip))
        except AgenError:
            continue  # ошибку чтения покажет сам узел
    if sample is None:
        sampled = {i for i, n in counts.items() if n > SAMPLE_FROM_ROWS}
        if not sampled:
            return None
        k = max(2, math.ceil(max(counts[i] for i in sampled) / SAMPLE_TARGET_ROWS))
    else:
        sampled = {i for i, n in counts.items() if n > 0}
        k = sample
    keys = {i: sample_keys(plan, i, sampled) for i in sampled}
    return SamplePlan(k=k, keys=keys, inputs=sampled)


def history_rows(engine: _Engine, iid: str, lower: date | None) -> int:
    """Число строк истории входа в границах чтения (кэшируется по отпечатку истории)."""
    cache = engine.opts.cache
    fp = engine.fingerprint(iid)
    key = make_key("rows", fp, lower, engine.period.end_exclusive) if fp else None
    if cache is not None and key is not None:
        hit = cache.get_meta(key)
        if hit is not None and "rows" in hit:
            return int(hit["rows"])
    pc = engine.plan.inputs[iid].schema.period_column
    n = int(engine.history.scan(iid, [pc], lower, engine.period.end_exclusive).select(pl.len()).collect().item())
    if cache is not None and key is not None:
        cache.put_meta(key, {"rows": n})
    return n
