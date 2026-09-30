"""Выполнение сценария: входы → наборы данных → показатели (ARCHITECTURE.md, раздел 6.4).

Каждый узел выполняется отдельно и получает статус. Ошибка узла помечает только зависимые
от него узлы: например, ошибка в обработке входа ``plan`` не мешает посчитать наборы и
показатели по входу ``sales``.

Результат обработки каждого входа материализуется в Parquet в рабочей папке, и наборы с
показателями читают уже его. Кэш узлов между запусками и восстановление после падения
процесса — на этапе M2.
"""

from __future__ import annotations

import contextlib
import math
import time
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import polars as pl

from autogenerator.contracts import (
    AgenError,
    DateSpan,
    DType,
    EngineResult,
    HistoryProvider,
    Issue,
    IssueLevel,
    NodeKind,
    NodeState,
    NodeStatus,
    Period,
    WindowSpec,
)
from autogenerator.plugin_host import PluginRegistry

from .analysis import DatasetPlan, InputPlan, MetricPlan, ScenarioPlan, WindowPlan
from .sqlexpr import SqlTranslator

BUCKETS = {"day": "1d", "week": "1w", "month": "1mo", "quarter": "1q", "year": "1y"}


class _StepContext:
    """Реализация ``StepContext`` для шагов из плагинов."""

    def __init__(self, engine: _Engine, ip: InputPlan, columns: list[str]):
        self.input_id = ip.spec.id
        self.period = engine.period
        self.period_column = ip.schema.period_column
        self._engine = engine
        self._tr = SqlTranslator(columns)

    def expr(self, sql: str) -> pl.Expr:
        return self._tr.expr(sql)

    def columns_in(self, sql: str) -> set[str]:
        return self._tr.columns_in(sql)

    def window(self, spec: WindowSpec | str) -> DateSpan:
        ws = spec if isinstance(spec, WindowSpec) else WindowSpec.model_validate(spec)
        plugin = self._engine.registry.window(ws.type)
        return plugin.resolve(self.period, plugin.parse_params(ws.params))

    def warn(self, message: str) -> None:
        self._engine.warn(f"input:{self.input_id}", message)


def _bound(value: date, dtype: DType | None) -> pl.Expr:
    if dtype == DType.DATETIME:
        return pl.lit(datetime.combine(value, dtime()), dtype=pl.Datetime("us"))
    return pl.lit(value, dtype=pl.Date())


def _scalar(v: Any) -> float | int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _span_text(s: DateSpan) -> str:
    start = s.start.strftime("%d.%m.%Y") if s.start else "начала истории"
    last = date.fromordinal(s.end_exclusive.toordinal() - 1).strftime("%d.%m.%Y")
    return f"{start}–{last}"


class _Engine:
    def __init__(
        self,
        plan: ScenarioPlan,
        registry: PluginRegistry,
        history: HistoryProvider,
        period: Period,
        workdir: Path,
    ):
        self.plan = plan
        self.registry = registry
        self.history = history
        self.period = period
        self.workdir = workdir
        self.result = EngineResult(period=period)
        self.status: dict[str, NodeStatus] = {}
        self.input_paths: dict[str, Path] = {}
        self._coverage_warned: set[tuple[str, DateSpan]] = set()

    # --- служебное ------------------------------------------------------------------

    def warn(self, node: str, message: str) -> None:
        self.result.issues.append(Issue(level=IssueLevel.WARNING, node=node, message=message))

    def set_status(self, st: NodeStatus) -> None:
        self.status[st.id] = st
        self.result.nodes.append(st)
        if st.state == NodeState.ERROR:
            self.result.issues.append(Issue(level=IssueLevel.ERROR, node=st.id, message=st.message or "ошибка"))

    def blocked(self, deps: list[str]) -> str | None:
        """Первый корень ошибки среди зависимостей узла."""
        for d in deps:
            st = self.status.get(d)
            if st is not None and st.state != NodeState.OK:
                return st.blocked_by or st.id
        return None

    def skip(self, node: str, kind: NodeKind, root: str) -> None:
        self.set_status(
            NodeStatus(
                id=node,
                kind=kind,
                state=NodeState.SKIPPED,
                blocked_by=root,
                message=f"не считался: зависит от «{root}», в котором ошибка",
            )
        )

    def resolve(self, wp: WindowPlan) -> DateSpan:
        return wp.plugin.resolve(self.period, wp.params)

    def check_coverage(self, input_id: str, span: DateSpan, node: str, what: str) -> None:
        """Предупредить, если окну нужны данные за даты, которых нет в загрузках."""
        if span.start is None or (input_id, span) in self._coverage_warned:
            return
        gaps: list[DateSpan] = []
        cursor = span.start
        for c in self.history.coverage(input_id):
            if c.start is not None and c.start > cursor and cursor < span.end_exclusive:
                gaps.append(DateSpan(start=cursor, end_exclusive=min(c.start, span.end_exclusive)))
            cursor = max(cursor, c.end_exclusive)
            if cursor >= span.end_exclusive:
                break
        if cursor < span.end_exclusive:
            gaps.append(DateSpan(start=cursor, end_exclusive=span.end_exclusive))
        if gaps:
            self._coverage_warned.add((input_id, span))
            missing = ", ".join(_span_text(g) for g in gaps)
            self.warn(node, f"{what}: нет загрузок входа «{input_id}» за {missing}")

    # --- входы -------------------------------------------------------------------

    def run_input(self, ip: InputPlan) -> None:
        node = f"input:{ip.spec.id}"
        t0 = time.perf_counter()
        # Какой отрезок истории нужен наборам и показателям этого входа.
        spans = []
        for n in self.plan.input_dependents(ip.spec.id):
            kind, nid = n.split(":", 1)
            wp = self.plan.datasets[nid].window if kind == "dataset" else self.plan.metrics[nid].window
            if wp is not None:
                # Окно, которое не вычисляется, сообщит об ошибке в своём узле; здесь его
                # просто не учитываем, и нижняя граница не проталкивается.
                with contextlib.suppress(Exception):
                    spans.append(self.resolve(wp))
        lower = None
        if spans and ip.row_local and all(s.start is not None for s in spans):
            lower = min(s.start for s in spans if s.start is not None)
        try:
            lf = self.history.scan(ip.spec.id, lower=lower, upper_exclusive=self.period.end_exclusive)
            rows_in = lf.select(pl.len()).collect().item()
            for sp in ip.steps:
                cols = lf.collect_schema().names()
                ctx = _StepContext(self, ip, cols)
                try:
                    lf = sp.plugin.apply(lf, sp.params, ctx)
                    lf.collect_schema()
                except AgenError as e:
                    raise AgenError(e.code, f"шаг «{sp.spec.id}» ({sp.spec.type}): {e.message}") from e
                except Exception as e:
                    raise RuntimeError(f"шаг «{sp.spec.id}» ({sp.spec.type}): {e}") from e
            out = self.workdir / "inputs" / f"{ip.spec.id}.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            lf.sink_parquet(out)
            rows_out = pl.scan_parquet(out).select(pl.len()).collect().item()
        except Exception as e:
            msg = e.message if isinstance(e, AgenError) else str(e)
            self.set_status(NodeStatus(id=node, kind=NodeKind.INPUT, state=NodeState.ERROR, message=msg))
            return
        self.input_paths[ip.spec.id] = out
        if rows_out == 0:
            self.warn(node, f"после обработки во входе «{ip.spec.id}» не осталось строк")
        self.set_status(
            NodeStatus(
                id=node,
                kind=NodeKind.INPUT,
                state=NodeState.OK,
                rows_in=rows_in,
                rows_out=rows_out,
                seconds=round(time.perf_counter() - t0, 3),
            )
        )

    def windowed(self, input_id: str, wp: WindowPlan | None, where: str | None, node: str, what: str) -> pl.LazyFrame:
        ip = self.plan.inputs[input_id]
        pc = ip.schema.period_column
        dtype = ip.schema.columns.get(pc)
        lf = pl.scan_parquet(self.input_paths[input_id])
        if wp is not None:
            span = self.resolve(wp)
            self.check_coverage(input_id, span, node, what)
            cond = pl.col(pc) < _bound(span.end_exclusive, dtype)
            if span.start is not None:
                cond = cond & (pl.col(pc) >= _bound(span.start, dtype))
            lf = lf.filter(cond.fill_null(False))
        if where:
            tr = SqlTranslator(lf.collect_schema().names())
            lf = lf.filter(tr.expr(where).fill_null(False))
        return lf

    # --- наборы данных ------------------------------------------------------------

    def run_dataset(self, dp: DatasetPlan) -> None:
        ds = dp.spec
        node = f"dataset:{ds.id}"
        root = self.blocked([f"input:{ds.input}"])
        if root:
            self.skip(node, NodeKind.DATASET, root)
            return
        t0 = time.perf_counter()
        try:
            what = f"набор «{ds.label or ds.id}» (окно {ds.window.describe()})"
            lf = self.windowed(ds.input, dp.window, ds.where, node, what)
            if dp.aggregates:
                keys = []
                for g in ds.group_by:
                    k = pl.col(g.column)
                    if g.bucket is not None:
                        k = k.dt.truncate(BUCKETS[g.bucket.value]).cast(pl.Date)
                    keys.append(k.alias(g.column))
                aggs = [agg.polars_expr(a.column).alias(a.output_name) for a, agg in dp.aggregates]
                out = lf.group_by(keys).agg(aggs) if keys else lf.select(aggs)
                default_sort = [g.column for g in ds.group_by]
            else:
                names = lf.collect_schema().names()
                cols = ds.columns or [c for c in names if not c.startswith("_")]
                out = lf.select(cols)
                default_sort = []
            if ds.sort:
                out = out.sort(
                    [s.column for s in ds.sort],
                    descending=[s.desc for s in ds.sort],
                    nulls_last=True,
                )
            elif default_sort:
                out = out.sort(default_sort, nulls_last=True)
            if ds.top:
                out = out.head(ds.top)
            df = out.collect()
        except Exception as e:
            msg = e.message if isinstance(e, AgenError) else str(e)
            self.set_status(NodeStatus(id=node, kind=NodeKind.DATASET, state=NodeState.ERROR, message=msg))
            return
        if df.height == 0:
            self.warn(node, f"набор «{ds.id}» пуст")
        self.result.datasets[ds.id] = df.to_arrow()
        self.set_status(
            NodeStatus(
                id=node,
                kind=NodeKind.DATASET,
                state=NodeState.OK,
                rows_out=df.height,
                seconds=round(time.perf_counter() - t0, 3),
            )
        )

    # --- показатели ---------------------------------------------------------------

    def run_metric(self, mp: MetricPlan) -> None:
        m = mp.spec
        node = f"metric:{m.id}"
        deps = [f"input:{m.input}"] if m.input else [f"metric:{r}" for r in sorted(mp.refs)]
        root = self.blocked(deps)
        if root:
            self.skip(node, NodeKind.METRIC, root)
            return
        t0 = time.perf_counter()
        try:
            if m.formula is not None:
                values = {r: self.result.metrics.get(r) for r in mp.refs}
                frame = pl.DataFrame({k: [v] for k, v in values.items()}, schema={k: pl.Float64 for k in values})
                value = _scalar(frame.select(SqlTranslator(values).expr(m.formula).alias("v")).item())
            else:
                assert m.input is not None and mp.aggregation is not None
                what = f"показатель «{m.label or m.id}» (окно {m.window.describe()})"
                lf = self.windowed(m.input, mp.window, m.where, node, what)
                value = _scalar(lf.select(mp.aggregation.polars_expr(m.column)).collect().item())
        except Exception as e:
            msg = e.message if isinstance(e, AgenError) else str(e)
            self.set_status(NodeStatus(id=node, kind=NodeKind.METRIC, state=NodeState.ERROR, message=msg))
            return
        self.result.metrics[m.id] = value
        if value is None:
            reason = "в формуле пустое значение или деление на ноль" if m.formula else "нет данных в окне"
            self.warn(node, f"показатель «{m.id}» пуст: {reason}; в тексте будет «нет данных»")
        self.set_status(
            NodeStatus(
                id=node,
                kind=NodeKind.METRIC,
                state=NodeState.OK,
                seconds=round(time.perf_counter() - t0, 3),
            )
        )

    def run(self) -> EngineResult:
        for ip in self.plan.inputs.values():
            self.run_input(ip)
        for dp in self.plan.datasets.values():
            self.run_dataset(dp)
        for mid in self.plan.metric_order:
            self.run_metric(self.plan.metrics[mid])
        return self.result


def execute(
    plan: ScenarioPlan,
    registry: PluginRegistry,
    history: HistoryProvider,
    period: Period,
    workdir: str | Path,
) -> EngineResult:
    """Выполнить разобранный сценарий за отчётный период ``period``.

    Ошибки в сценарии (``plan.errors``) останавливают выполнение до чтения данных. Ошибки в
    данных и узлах не бросаются: они — в статусах узлов и ``result.issues``.
    """
    plan.raise_on_errors()
    return _Engine(plan, registry, history, period, Path(workdir)).run()
