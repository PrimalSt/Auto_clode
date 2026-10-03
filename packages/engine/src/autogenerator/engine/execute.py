"""Выполнение сценария: входы → наборы данных → показатели (ARCHITECTURE.md, раздел 6.4).

Каждый узел выполняется отдельно и получает статус. Ошибка узла помечает только зависимые
от него узлы: например, ошибка в обработке входа ``plan`` не мешает посчитать наборы и
показатели по входу ``sales``.

Шаги одного входа сливаются в один ленивый план Polars; результат обработки входа
записывается в Parquet (в кэш узлов, если он задан), и наборы с показателями читают уже
его. Операции над всей таблицей на больших данных шаги отдают DuckDB, код на Python
выполняется в отдельном процессе (``context``).

Нижняя граница чтения истории — начало самого раннего окна среди наборов и показателей
входа, включая окна периодов сравнения, и сдвинутое назад на глубину шагов (``lookback``).
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow.parquet as pq

from autogenerator.contracts import (
    AgenError,
    DatasetSpec,
    DateSpan,
    EngineResult,
    ErrorCode,
    HistoryProvider,
    Issue,
    IssueLevel,
    NodeKind,
    NodeState,
    NodeStatus,
    Period,
    PeriodUnit,
    PluginKind,
    StepStat,
    WindowSpec,
)
from autogenerator.contracts.periods import CALENDAR_UNITS, unit_shift, unit_start
from autogenerator.plugin_host import PluginRegistry

from . import datasets as dsx
from .analysis import DatasetPlan, InputPlan, MetricPlan, ScenarioPlan, WindowPlan
from .cache import NodeCache, make_key
from .context import StepRunContext, context_json, user_context
from .duck import Duck
from .isolated import run_job
from .resources import Limits
from .sqlexpr import SqlTranslator, UnsupportedExpression
from .usercode import scalar

LARGE_ROWS = 2_000_000
"""С какого числа строк входа шаги отдают операции над всей таблицей DuckDB."""


@dataclass
class SamplePlan:
    """Выборка для превью: строки с ``hash(ключ) % k == 0``."""

    k: int
    keys: dict[str, list[str]] = field(default_factory=dict)
    inputs: set[str] = field(default_factory=set)


@dataclass
class EngineOptions:
    limits: Limits | None = None
    temp_dir: Path | None = None
    cache: NodeCache | None = None
    large_rows: int = LARGE_ROWS
    code_timeout: float | None = 1800.0
    preview: bool = False
    """Превью: предупреждения о данных не выдаются (часто это выборка)."""
    anchor: date | None = None
    nodes: list[str] | None = None
    """Считать только эти узлы и то, от чего они зависят."""
    stop_after: str | None = None
    """Остановить обработку входа после шага: ``input:sales/step:dedupe``."""
    count_steps: bool = False
    """Считать строки после каждого шага (превью)."""
    sample: SamplePlan | None = None


def _bound(value: date, dtype: Any) -> pl.Expr:
    if isinstance(dtype, pl.Datetime):
        return pl.lit(datetime.combine(value, dtime()), dtype=pl.Datetime("us"))
    return pl.lit(value, dtype=pl.Date())


def _span_text(s: DateSpan) -> str:
    start = s.start.strftime("%d.%m.%Y") if s.start else "начала истории"
    last = date.fromordinal(s.end_exclusive.toordinal() - 1).strftime("%d.%m.%Y")
    return f"{start}–{last}"


def _message(e: BaseException) -> str:
    return e.message if isinstance(e, AgenError) else f"{type(e).__name__}: {e}"


def sample_filter(keys: list[str], schema: pl.Schema, k: int) -> pl.Expr:
    """Условие выборки: одинаковая функция для всех входов, поэтому пары ключей объединения
    и дубликаты попадают в выборку вместе."""
    if not keys:
        keys = [c for c in ("_upload_id", "_row") if c in schema]
    parts = []
    for c in keys:
        e = pl.col(c)
        if isinstance(schema.get(c), pl.Datetime):
            e = e.cast(pl.Date)
        parts.append(e.cast(pl.String).fill_null("\x00"))
    return pl.concat_str(parts, separator="\x1f").hash(seed=0) % k == 0


class _Engine:
    def __init__(
        self,
        plan: ScenarioPlan,
        registry: PluginRegistry,
        history: HistoryProvider,
        period: Period,
        workdir: Path,
        opts: EngineOptions,
    ):
        self.plan = plan
        self.registry = registry
        self.history = history
        self.period = period
        self.workdir = workdir
        self.opts = opts
        # Своя временная папка у каждого запуска: превью и запуск могут идти одновременно.
        self.tmp_dir = (opts.temp_dir or workdir / "tmp") / f"run-{uuid.uuid4().hex[:8]}"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.duck = Duck(self.tmp_dir / "duckdb", opts.limits)
        settings = plan.scenario.settings
        self.anchor = opts.anchor or (date.today() if settings.relative_to == "run_date" else period.last_day)
        self.result = EngineResult(period=period)
        self.status: dict[str, NodeStatus] = {}
        self.input_paths: dict[str, Path] = {}
        self.node_keys: dict[str, str | None] = {}
        self.large: dict[str, bool] = {}
        self.code_seconds: dict[str, float] = {}
        self.frames: dict[str, pl.DataFrame] = {}
        self.values: dict[tuple[str, str], float | int | None] = {}
        self.read_rows: dict[str, int] = {}
        self._coverage_warned: set[tuple[str, DateSpan]] = set()
        self.needed = plan.closure(opts.nodes) if opts.nodes is not None else None
        self.periods = self.requested_periods()

    # --- служебное ------------------------------------------------------------------

    def issue(self, level: IssueLevel, node: str, message: str) -> None:
        self.result.issues.append(Issue(level=level, node=node, message=message))

    def warn(self, node: str, message: str) -> None:
        self.issue(IssueLevel.WARNING, node, message)

    def info(self, node: str, message: str) -> None:
        self.issue(IssueLevel.INFO, node, message)

    def set_status(self, st: NodeStatus) -> None:
        self.status[st.id] = st
        self.result.nodes.append(st)
        if st.state == NodeState.ERROR:
            self.result.issues.append(Issue(level=IssueLevel.ERROR, node=st.id, message=st.message or "ошибка"))

    def fail(self, node: str, kind: NodeKind, e: BaseException) -> None:
        details = e.details if isinstance(e, AgenError) else {}
        for line in details.get("logs") or []:
            self.info(node, line)
        self.set_status(NodeStatus(id=node, kind=kind, state=NodeState.ERROR, message=_message(e)))
        if details.get("traceback"):
            self.info(node, "след вызовов:\n" + str(details["traceback"]).rstrip())

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

    def wanted(self, node: str) -> bool:
        return self.needed is None or node in self.needed

    def resolve_window(self, ws: WindowSpec, period: Period) -> DateSpan:
        plugin = self.registry.window(ws.type)
        return plugin.resolve(period, plugin.parse_params(ws.params))

    def resolve(self, wp: WindowPlan, period: Period) -> DateSpan:
        return wp.plugin.resolve(period, wp.params)

    def check_coverage(self, input_id: str, span: DateSpan, node: str, what: str) -> None:
        """Предупредить, если окну нужны данные за даты, которых нет в загрузках."""
        if self.opts.preview or span.start is None or (input_id, span) in self._coverage_warned:
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

    # --- границы и ключи кэша --------------------------------------------------------

    def requested_periods(self) -> dict[str, list[Period]]:
        """За какие периоды считается каждый узел: отчётный, а с ``compare`` — ещё и периоды
        сравнения (формула со сравнением запрашивает свои показатели за сдвинутый период)."""
        plan = self.plan
        req: dict[str, set[Period]] = {f"metric:{m}": {self.period} for m in plan.metrics}
        for mid in reversed(plan.metric_order):
            mp = plan.metrics[mid]
            periods = req[f"metric:{mid}"]
            if mp.base is not None and mp.shift is not None:
                req[f"metric:{mp.base}"] |= {dsx.shifted_period(q, mp.shift) for q in periods}
            elif mp.base is not None or mp.spec.kind == "formula":
                for r in [*([mp.base] if mp.base else []), *mp.refs]:
                    req[f"metric:{r}"] |= periods
        for did, dp in plan.datasets.items():
            req[f"dataset:{did}"] = {self.period} | {dsx.shifted_period(self.period, c.window) for c in dp.spec.compare}
        return {k: sorted(v, key=lambda p: (p.start, p.end_exclusive)) for k, v in req.items()}

    def lower_bound(self, ip: InputPlan) -> date | None:
        iid = ip.spec.id
        if any(iid in other.deps for other in self.plan.inputs.values()):
            return None  # вход нужен шагам другого входа целиком
        lookback = ip.lookback
        if lookback is None:
            return None
        starts: list[date] = []
        for n in self.plan.input_dependents(iid):
            kind, nid = n.split(":", 1)
            wp = self.plan.datasets[nid].window if kind == "dataset" else self.plan.metrics[nid].window
            if wp is None:
                continue
            for p in self.periods.get(n, [self.period]):
                try:
                    span = self.resolve(wp, p)
                except Exception:
                    return None  # окно с ошибкой сообщит о ней в своём узле
                if span.start is None:
                    return None
                starts.append(span.start)
        if not starts:
            return None
        lower = min(starts)
        if lookback:
            unit = ip.schema.period_unit if ip.schema.period_unit in CALENDAR_UNITS else PeriodUnit.MONTH
            lower = unit_shift(unit_start(lower, unit), unit, -lookback)
        return lower

    def fingerprint(self, input_id: str) -> str | None:
        fn = getattr(self.history, "fingerprint", None)
        if fn is None:
            return None
        try:
            value = fn(input_id)
        except Exception:
            return None
        return str(value) if value else None

    def input_key(self, ip: InputPlan, lower: date | None) -> str | None:
        iid = ip.spec.id
        fp = self.fingerprint(iid)
        if fp is None or self.opts.cache is None:
            return None
        # Шаг, которому нельзя брать результат из кэша (код читает внешние файлы), выключает
        # кэш для всего входа и для всего, что от него зависит: у них ключа тоже не будет.
        if any(not sp.cacheable for sp in ip.steps):
            return None
        deps = [self.node_keys.get(f"input:{d}") for d in ip.deps]
        if any(d is None for d in deps):
            return None
        sample = self.opts.sample
        sample_part = (sample.k, sample.keys.get(iid)) if sample and iid in sample.inputs else None
        return make_key(
            "input",
            fp,
            ip.spec.source,
            [self.step_part(sp) for sp in ip.steps],
            sorted(ip.source_columns) if ip.source_columns is not None else None,
            lower,
            self.period.end_exclusive,
            self.period.key,
            self.anchor,
            deps,
            sample_part,
            # Превью считает строки после каждого шага и не пишет часть предупреждений
            # (о новых загрузках, о нераспознанных значениях): его результат — отдельная запись.
            self.opts.preview,
            self.opts.count_steps,
        )

    def step_part(self, sp: Any) -> list[Any]:
        return [sp.spec.model_dump(mode="json"), self.registry.version(PluginKind.STEP, sp.spec.type)]

    # --- входы -------------------------------------------------------------------

    def sample_frame(self, ip: InputPlan, lower: date | None, sample: SamplePlan) -> pl.LazyFrame:
        """Выборка истории входа (всех столбцов) — из кэша или построенная заново."""
        iid = ip.spec.id
        keys = sample.keys.get(iid, [])
        fp = self.fingerprint(iid)
        cache = self.opts.cache
        key = make_key("sample", fp, lower, self.period.end_exclusive, sample.k, keys) if fp else None
        if cache is not None and key is not None:
            hit = cache.get(key)
            if hit is not None:
                return pl.scan_parquet(hit[0])
        lf = self.history.scan(iid, None, lower, self.period.end_exclusive)
        lf = lf.filter(sample_filter(keys, lf.collect_schema(), sample.k))
        if cache is not None and key is not None:
            return pl.scan_parquet(cache.put(key, lf))
        path = self.tmp_dir / f"sample-{iid}.parquet"
        lf.sink_parquet(path)
        return pl.scan_parquet(path)

    def scan_input(self, ip: InputPlan, lower: date | None) -> pl.LazyFrame:
        iid = ip.spec.id
        cols = sorted(ip.source_columns) if ip.source_columns is not None else None
        sample = self.opts.sample
        if sample is not None and iid in sample.inputs:
            lf = self.sample_frame(ip, lower, sample)
            if cols is not None:
                names = lf.collect_schema().names()
                wanted = dict.fromkeys([ip.schema.period_column, *cols, "_upload_id", "_upload_seq", "_row"])
                lf = lf.select([c for c in wanted if c in names])
            return lf
        return self.history.scan(iid, cols, lower, self.period.end_exclusive)

    def input_frame(self, input_id: str, node: str) -> pl.LazyFrame:
        path = self.input_paths.get(input_id)
        if path is None:
            raise AgenError(ErrorCode.NODE_FAILED, f"вход «{input_id}» не посчитан", details={"node": node})
        return pl.scan_parquet(path)

    def step_stats(self, ip: InputPlan, done: list[StepStat]) -> list[StepStat]:
        """Статистика всех шагов входа, включая отключённые."""
        by_id = {s.id: s for s in done}
        out = []
        for st in ip.spec.pipeline:
            if st.id in by_id:
                out.append(by_id[st.id])
            elif not st.enabled:
                out.append(StepStat(id=st.id, type=st.type, enabled=False))
        return out

    def run_input(self, ip: InputPlan) -> None:
        iid = ip.spec.id
        node = f"input:{iid}"
        root = self.blocked([f"input:{d}" for d in ip.deps])
        if root:
            self.skip(node, NodeKind.INPUT, root)
            return
        t0 = time.perf_counter()
        stop = self.opts.stop_after if (self.opts.stop_after or "").startswith(node + "/") else None
        stats: list[StepStat] = []
        try:
            lower = self.lower_bound(ip)
            key = self.input_key(ip, lower)
            self.node_keys[node] = key
            # Превью после шага — своя запись кэша: результат входа до этого шага.
            if key is not None and stop is not None:
                key = make_key("stop", key, stop)
            cache = self.opts.cache
            if key is not None and cache is not None:
                hit = cache.get(key)
                if hit is not None:
                    path, info = hit
                    self.input_paths[iid] = path
                    self.large[iid] = bool(info.get("large"))
                    self.read_rows[iid] = int(info.get("rows_in") or 0)
                    self.result.steps[iid] = [StepStat.model_validate(s) for s in info.get("steps", [])]
                    self.result.issues += [Issue.model_validate(i) for i in info.get("issues", [])]
                    self.set_status(
                        NodeStatus(
                            id=node,
                            kind=NodeKind.INPUT,
                            state=NodeState.OK,
                            rows_in=info.get("rows_in"),
                            rows_out=info.get("rows_out"),
                            seconds=round(time.perf_counter() - t0, 3),
                        )
                    )
                    return
            issues_from = len(self.result.issues)
            lf = self.scan_input(ip, lower)
            rows_in = int(lf.select(pl.len()).collect().item())
            self.read_rows[iid] = rows_in
            self.large[iid] = rows_in > self.opts.large_rows
            frames = [lf]
            prefix = key
            for sp in ip.steps:
                prefix = make_key("prefix", prefix, self.step_part(sp)) if prefix and sp.cacheable else None
                st = StepStat(id=sp.spec.id, type=sp.spec.type)
                ts = time.perf_counter()
                try:
                    ctx = StepRunContext(self, ip, sp, lf, prefix)
                    lf = sp.plugin.apply(lf, sp.params, ctx)
                    lf.collect_schema()
                except Exception as e:
                    st.error = _message(e)
                    stats.append(st)
                    msg = f"шаг «{sp.spec.id}» ({sp.spec.type}): {_message(e)}"
                    if isinstance(e, AgenError):
                        raise AgenError(e.code, msg, details=e.details, hint=e.hint) from e
                    raise AgenError(ErrorCode.NODE_FAILED, msg) from e
                st.seconds = round(time.perf_counter() - ts, 3)
                stats.append(st)
                frames.append(lf)
                if stop is not None and sp.node == stop:
                    break
            rows_out: int | None = None
            if self.opts.count_steps:
                counts = [int(c.item()) for c in pl.collect_all([f.select(pl.len()) for f in frames])]
                for i, st in enumerate(stats):
                    st.rows_before, st.rows_after = counts[i], counts[i + 1]
                rows_out = counts[-1]
            steps = self.step_stats(ip, stats)
            info = {
                "large": self.large[iid],
                "rows_in": rows_in,
                "steps": [s.model_dump(mode="json") for s in steps],
                # Предупреждения шагов повторяются, когда результат берётся из кэша.
                "issues": [
                    i.model_dump(mode="json")
                    for i in self.result.issues[issues_from:]
                    if i.node == node or (i.node or "").startswith(node + "/")
                ],
            }
            if key is not None and cache is not None:
                path = cache.put(key, lf, info)
            else:
                path = self.workdir / "inputs" / (f"{iid}.parquet" if stop is None else f"{iid}-до-шага.parquet")
                path.parent.mkdir(parents=True, exist_ok=True)
                lf.sink_parquet(path)
            if rows_out is None:
                rows_out = int(pq.ParquetFile(path).metadata.num_rows)
            if key is not None and cache is not None:
                cache.put_meta(key, {**info, "rows_out": rows_out})
        except Exception as e:
            self.result.steps[iid] = self.step_stats(ip, stats)
            self.fail(node, NodeKind.INPUT, e)
            return
        self.input_paths[iid] = path
        self.result.steps[iid] = steps
        if rows_out == 0 and not self.opts.preview:
            self.warn(node, f"после обработки во входе «{iid}» не осталось строк")
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

    # --- окна и фильтры -----------------------------------------------------------

    def filter_sql(self, lf: pl.LazyFrame, where: str, tag: str) -> pl.LazyFrame:
        tr = SqlTranslator(dict(lf.collect_schema()))
        try:
            return lf.filter(tr.expr(where).fill_null(False))
        except UnsupportedExpression:
            pass
        src = self.tmp_dir / f"{tag}-where-in.parquet"
        out = self.tmp_dir / f"{tag}-where-out.parquet"
        lf.sink_parquet(src)
        self.duck.run(f"SELECT * FROM data WHERE ({where})", {"data": src}, out)
        return pl.scan_parquet(out)

    def windowed(
        self, input_id: str, wp: WindowPlan | None, where: str | None, node: str, what: str, period: Period
    ) -> pl.LazyFrame:
        ip = self.plan.inputs[input_id]
        pc = ip.schema.period_column
        lf = self.input_frame(input_id, node)
        schema = lf.collect_schema()
        if wp is not None:
            span = self.resolve(wp, period)
            if pc not in schema:
                raise AgenError(
                    ErrorCode.NODE_FAILED,
                    f"после обработки во входе «{input_id}» нет столбца периода «{pc}»: окно не применить",
                )
            self.check_coverage(input_id, span, node, what)
            dtype = schema[pc]
            cond = pl.col(pc) < _bound(span.end_exclusive, dtype)
            if span.start is not None:
                cond = cond & (pl.col(pc) >= _bound(span.start, dtype))
            lf = lf.filter(cond.fill_null(False))
        if where:
            lf = self.filter_sql(lf, where, node.replace(":", "-"))
        return lf

    def code_tables(
        self, refs: list[str], wp: WindowPlan | None, node: str, what: str, period: Period
    ) -> dict[str, Path]:
        """Таблицы для запроса или кода: входы в окне узла и готовые наборы."""
        out: dict[str, Path] = {}
        tag = node.replace(":", "-")
        for ref in refs:
            if ref.startswith("dataset:"):
                name = ref.split(":", 1)[1]
                if name not in self.frames:
                    raise AgenError(ErrorCode.NODE_FAILED, f"набор «{name}» не посчитан")
                p = self.tmp_dir / f"{tag}-{name}.parquet"
                self.frames[name].write_parquet(p)
            else:
                name = ref
                p = self.tmp_dir / f"{tag}-{name}-{period.key}.parquet"
                self.windowed(ref, wp, None, node, what, period).sink_parquet(p)
            out[name] = p
        return out

    def check_tables(self, refs: list[str], wp: WindowPlan | None, node: str, what: str, period: Period) -> None:
        """Предупреждения о пропусках в загрузках, когда результат узла взят из кэша."""
        if wp is None:
            return
        span = self.resolve(wp, period)
        for ref in refs:
            if not ref.startswith("dataset:"):
                self.check_coverage(ref, span, node, what)

    def run_code_node(
        self, node: str, code: str, frame: str, mode: str, tables: dict[str, Path], period: Period, output: Path | None
    ) -> dict[str, Any]:
        ctx = user_context(self, node, period)
        job = {
            "mode": mode,
            "node": node,
            "code": code,
            "frame": frame,
            "tables": {k: str(v) for k, v in tables.items()},
            "output": str(output) if output else None,
            "context": context_json(ctx),
        }
        limits = self.duck.limits
        tag = node.replace(":", "-")
        try:
            res = run_job(
                job, self.tmp_dir / f"job-{tag}.json", timeout=self.opts.code_timeout, memory=limits.code_memory
            )
        except AgenError as e:
            for w in e.details.get("warnings") or []:
                self.warn(node, w)
            raise
        self.replay(node, res)
        return res

    def replay(self, node: str, info: dict[str, Any]) -> None:
        """Записи журнала и предупреждения кода — после запуска или из кэша вместе с результатом."""
        for line in info.get("logs") or []:
            self.info(node, line)
        for w in info.get("warnings") or []:
            self.warn(node, w)

    # --- наборы данных ------------------------------------------------------------

    def dataset_key(self, dp: DatasetPlan) -> str | None:
        if self.opts.cache is None:
            return None
        deps = [self.node_keys.get(d) for d in dp.deps]
        if any(d is None for d in deps):
            return None
        return make_key(
            "dataset",
            dp.spec.model_dump(mode="json"),
            deps,
            self.period.key,
            self.anchor,
            self.plugin_versions(dp.window, [a.fn for a, _ in dp.aggregates]),
        )

    def run_dataset(self, dp: DatasetPlan) -> None:
        ds = dp.spec
        node = f"dataset:{ds.id}"
        root = self.blocked(dp.deps)
        if root:
            self.skip(node, NodeKind.DATASET, root)
            return
        t0 = time.perf_counter()
        key = self.dataset_key(dp)
        self.node_keys[node] = key
        try:
            if ds.type == "sql":
                df = self.sql_dataset(dp, node)
            elif ds.type == "python":
                df = self.python_dataset(dp, node, key)
            else:
                df = self.table_dataset(dp, node)
        except Exception as e:
            self.fail(node, NodeKind.DATASET, e)
            return
        self.frames[ds.id] = df
        self.result.datasets[ds.id] = df.to_arrow()
        if df.height == 0 and not self.opts.preview:
            self.warn(node, f"набор «{ds.id}» пуст")
        self.set_status(
            NodeStatus(
                id=node,
                kind=NodeKind.DATASET,
                state=NodeState.OK,
                rows_out=df.height,
                seconds=round(time.perf_counter() - t0, 3),
            )
        )

    def what(self, kind: str, label: str, wp: WindowPlan | None) -> str:
        return f"{kind} «{label}»" + (f" (окно {wp.spec.describe()})" if wp is not None else "")

    def sql_dataset(self, dp: DatasetPlan, node: str) -> pl.DataFrame:
        ds = dp.spec
        assert ds.query is not None
        tables = self.code_tables(
            dp.tables, dp.window, node, self.what("набор", ds.label or ds.id, dp.window), self.period
        )
        out = self.tmp_dir / f"{node.replace(':', '-')}-out.parquet"
        self.duck.run(ds.query, dict(tables), out)
        return pl.read_parquet(out)

    def python_dataset(self, dp: DatasetPlan, node: str, key: str | None) -> pl.DataFrame:
        ds = dp.spec
        assert ds.code is not None
        cache = self.opts.cache
        if key is not None and cache is not None:
            hit = cache.get(key)
            if hit is not None:
                self.check_tables(
                    dp.tables, dp.window, node, self.what("набор", ds.label or ds.id, dp.window), self.period
                )
                self.replay(node, hit[1])
                return pl.read_parquet(hit[0])
        tables = self.code_tables(
            dp.tables, dp.window, node, self.what("набор", ds.label or ds.id, dp.window), self.period
        )
        out = self.tmp_dir / f"{node.replace(':', '-')}-out.parquet"
        res = self.run_code_node(node, ds.code, ds.frame, "build", tables, self.period, out)
        if key is not None and cache is not None:
            out = cache.put_file(key, out, {k: res.get(k) or [] for k in ("logs", "warnings")})
        return pl.read_parquet(out)

    def small_formula(self, sql: str, df: pl.DataFrame, name: str) -> pl.DataFrame:
        """Формула над небольшой таблицей в DuckDB (если её не перевести в Polars)."""
        table = self.duck.small(f'SELECT *, ({sql}) AS "{name}" FROM data', {"data": df.to_arrow()})
        out = pl.from_arrow(table)
        assert isinstance(out, pl.DataFrame)
        return out

    def formula(self, df: pl.DataFrame, name: str, sql: str) -> pl.DataFrame:
        if name in df.columns:
            df = df.drop(name)
        return dsx.formula_column(df, name, sql, lambda q, d: self.small_formula(q, d, name))

    def sort(self, df: pl.DataFrame, ds: DatasetSpec) -> pl.DataFrame:
        specs = ds.sort
        if specs:
            cols = [s.column for s in specs if s.column in df.columns]
            if len(cols) == len(specs):
                return df.sort(cols, descending=[s.desc for s in specs], nulls_last=True, maintain_order=True)
        default = [g.column for g in ds.group_by if g.column in df.columns]
        return df.sort(default, nulls_last=True, maintain_order=True) if default else df

    def table_dataset(self, dp: DatasetPlan, node: str) -> pl.DataFrame:
        ds = dp.spec
        assert ds.input is not None
        ip = self.plan.inputs[ds.input]
        pc = ip.schema.period_column
        what = self.what("набор", ds.label or ds.id, dp.window)
        lf = self.windowed(ds.input, dp.window, ds.where, node, what, self.period)
        shares = sorted({d.column for d in ds.derive if d.fn == "share" and d.column})
        if not dp.aggregates:
            names = lf.collect_schema().names()
            cols = ds.columns or [c for c in names if not c.startswith("_")]
            totals = lf.select([pl.col(c).sum() for c in shares]).collect().row(0, named=True) if shares else {}
            df = self.sort(lf.select(cols).collect(), ds)
            df = dsx.derive(df, ds.derive, totals, self.formula)
            df = self.sort(df, ds)
            return df.head(ds.top) if ds.top else df
        df = dsx.aggregate(lf, ds, dp.aggregates, pc)
        shifted: dict[str, pl.LazyFrame] = {}
        for c in ds.compare:
            p2 = dsx.shifted_period(self.period, c.window)
            lf2 = self.windowed(ds.input, dp.window, ds.where, node, f"{what}, сравнение {c.window}", p2)
            shifted[c.tag] = lf2
            prev = dsx.aggregate(lf2, ds, dp.aggregates, pc, offset=dsx.shift_offset(self.period, c.window))
            df = dsx.add_compare(df, prev, ds, c.tag)
        if ds.pivot is not None:
            return self.sort(dsx.pivot(df, ds), ds)
        totals = {c: df[c].sum() for c in shares}
        df = self.sort(df, ds)
        df = dsx.derive(df, ds.derive, totals, self.formula)
        df = self.sort(df, ds)
        if not ds.top or df.height <= ds.top:
            return df
        top = df.head(ds.top)
        if ds.others is None:
            return top
        return self.with_others(df, top, lf, shifted, dp, totals)

    def with_others(
        self,
        full: pl.DataFrame,
        top: pl.DataFrame,
        lf: pl.LazyFrame,
        shifted: dict[str, pl.LazyFrame],
        dp: DatasetPlan,
        totals: dict[str, Any],
    ) -> pl.DataFrame:
        """Топ-N и строка «Прочие»: агрегаты пересчитаны по строкам, не вошедшим в топ."""
        ds = dp.spec
        assert ds.others is not None
        g = ds.group_by[0].column
        values = top[g].to_list()
        flat = DatasetSpec(id=ds.id, input=ds.input, aggregate=ds.aggregate)
        pc = self.plan.inputs[ds.input or ""].schema.period_column

        def rest(src: pl.LazyFrame, offset: str | None) -> pl.DataFrame:
            keyed = src.with_columns(dsx.group_keys(ds, src.collect_schema(), offset))
            return dsx.aggregate(keyed.filter(dsx.others_mask(g, values)), flat, dp.aggregates, pc)

        row = rest(lf, None)
        for c in ds.compare:
            prev = rest(shifted[c.tag], dsx.shift_offset(self.period, c.window))
            prev = prev.rename({a.output_name: f"{a.output_name}_{c.tag}" for a in ds.aggregate})
            row = pl.concat([row, prev], how="horizontal_extend")
            for a in ds.aggregate:
                row = row.with_columns(dsx.change_columns(a.output_name, c.tag))
        for d in ds.derive:
            name = d.output_name
            if d.fn == "share":
                total = totals.get(d.column or "")
                value = (pl.col(d.column or "").cast(pl.Float64) / total) if total else pl.lit(None, pl.Float64)
                row = row.with_columns(value.alias(name))
            elif d.fn == "cumsum":
                row = row.with_columns(pl.lit(full[name][-1]).alias(name))
            elif d.fn == "rank":
                row = row.with_columns(pl.lit(None, pl.Int64).alias(name))
            else:
                assert d.expr is not None
                row = self.formula(row, name, d.expr)
        row = row.with_columns(pl.lit(ds.others).alias(g))
        if top.schema[g] != pl.String:
            top = top.with_columns(pl.col(g).cast(pl.String))
        row = row.select(
            [pl.col(c).cast(top.schema[c]) if c in row.columns else pl.lit(None).alias(c) for c in top.columns]
        )
        return pl.concat([top, row], how="vertical_relaxed")

    # --- показатели ---------------------------------------------------------------

    def metric_key(self, mp: MetricPlan, period: Period) -> str | None:
        if self.opts.cache is None:
            return None
        deps = [self.node_keys.get(d) for d in mp.deps]
        if any(d is None for d in deps):
            return None
        fns = [mp.spec.fn] if mp.spec.fn else []
        return make_key(
            "metric",
            mp.spec.model_dump(mode="json"),
            deps,
            period.key,
            self.anchor,
            self.plugin_versions(mp.window, fns),
        )

    def plugin_versions(self, wp: WindowPlan | None, aggregations: list[str]) -> list[str]:
        """Версии плагинов окна и агрегатов узла: обновлённый плагин даёт новый ключ кэша."""
        out = [f"window:{wp.spec.type}={self.registry.version(PluginKind.WINDOW, wp.spec.type)}"] if wp else []
        return out + [f"agg:{fn}={self.registry.version(PluginKind.AGGREGATION, fn)}" for fn in aggregations]

    def run_metric(self, mp: MetricPlan) -> None:
        m = mp.spec
        node = f"metric:{m.id}"
        root = self.blocked(mp.deps)
        if root:
            self.skip(node, NodeKind.METRIC, root)
            return
        t0 = time.perf_counter()
        try:
            value = self.metric_value(m.id, self.period)
        except Exception as e:
            self.fail(node, NodeKind.METRIC, e)
            return
        self.result.metrics[m.id] = value
        if value is None and not self.opts.preview:
            if mp.base is not None:
                reason = (
                    "нет данных за период сравнения" if mp.shift else "нет значения за один из периодов или оно равно 0"
                )
            elif m.kind == "formula":
                reason = "в формуле пустое значение или деление на ноль"
            else:
                reason = "нет данных в окне"
            self.warn(node, f"показатель «{m.id}» пуст: {reason}; в тексте будет «нет данных»")
        self.set_status(
            NodeStatus(id=node, kind=NodeKind.METRIC, state=NodeState.OK, seconds=round(time.perf_counter() - t0, 3))
        )

    def metric_value(self, mid: str, period: Period) -> float | int | None:
        k = (mid, period.key)
        if k in self.values:
            return self.values[k]
        mp = self.plan.metrics[mid]
        if mp.base is not None:
            if mp.shift is not None:
                value = self.metric_value(mp.base, dsx.shifted_period(period, mp.shift))
            else:
                cur = self.metric_value(mp.base, period)
                prev = self.metric_value(next(iter(mp.refs)), period)
                if cur is None or prev is None:
                    value = None
                elif mp.change == "change":
                    value = cur - prev
                else:
                    value = None if prev == 0 else cur / prev - 1
        else:
            value = self.compute_metric(mp, period)
        self.values[k] = value
        return value

    def compute_metric(self, mp: MetricPlan, period: Period) -> float | int | None:
        m = mp.spec
        node = f"metric:{m.id}"
        what = self.what("показатель", m.label or m.id, mp.window)
        if period != self.period:
            what += f", период {period.key}"
        kind = m.kind
        if kind == "formula":
            assert m.formula is not None
            values = {r: self.metric_value(r, period) for r in sorted(mp.refs)}
            frame = pl.DataFrame({k: [v] for k, v in values.items()}, schema={k: pl.Float64 for k in values})
            if not values:
                frame = pl.DataFrame({"__row": [0]})
            out = self.formula(frame, "__v", m.formula)
            return scalar(out["__v"][0])
        if kind == "input":
            assert m.input is not None and mp.aggregation is not None
            lf = self.windowed(m.input, mp.window, m.where, node, what, period)
            if mp.aggregation.needs_order:
                lf = dsx.order_rows(lf, self.plan.inputs[m.input].schema.period_column)
            return scalar(lf.select(mp.aggregation.polars_expr(m.column)).collect().item())
        if kind == "dataset":
            assert m.dataset is not None and mp.aggregation is not None
            if period != self.period:
                raise AgenError(ErrorCode.NODE_FAILED, "показатель из набора считается только за отчётный период")
            if m.dataset not in self.frames:
                raise AgenError(ErrorCode.NODE_FAILED, f"набор «{m.dataset}» не посчитан")
            dlf = self.frames[m.dataset].lazy()
            if m.where:
                dlf = self.filter_sql(dlf, m.where, node.replace(":", "-"))
            return scalar(dlf.select(mp.aggregation.polars_expr(m.column)).collect().item())
        key = self.metric_key(mp, period)
        cache = self.opts.cache
        if key is not None and cache is not None:
            hit = cache.get_meta(key)
            if hit is not None and "value" in hit:
                self.check_tables(mp.tables, mp.window, node, what, period)
                self.replay(node, hit)
                return scalar(hit["value"])
        tables = self.code_tables(mp.tables, mp.window, node, what, period)
        info: dict[str, Any] = {}
        if kind == "sql":
            assert m.query is not None
            value = scalar(self.duck.scalar(m.query, dict(tables)))
        else:
            assert m.code is not None
            res = self.run_code_node(node, m.code, m.frame, "value", tables, period, None)
            value = scalar(res.get("value"))
            info = {k: res.get(k) or [] for k in ("logs", "warnings")}
        if key is not None and cache is not None:
            cache.put_meta(key, {**info, "value": value})
        return value

    def run(self) -> EngineResult:
        try:
            for iid in self.plan.input_order:
                if self.wanted(f"input:{iid}"):
                    self.run_input(self.plan.inputs[iid])
            for did in self.plan.dataset_order:
                if self.wanted(f"dataset:{did}"):
                    self.run_dataset(self.plan.datasets[did])
            for mid in self.plan.metric_order:
                if self.wanted(f"metric:{mid}") and mid in self.plan.metrics:
                    self.run_metric(self.plan.metrics[mid])
        finally:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
        return self.result


def execute(
    plan: ScenarioPlan,
    registry: PluginRegistry,
    history: HistoryProvider,
    period: Period,
    workdir: str | Path,
    options: EngineOptions | None = None,
) -> EngineResult:
    """Выполнить разобранный сценарий за отчётный период ``period``.

    Ошибки в сценарии (``plan.errors``) останавливают выполнение до чтения данных. Ошибки в
    данных и узлах не бросаются: они — в статусах узлов и ``result.issues``.
    """
    plan.raise_on_errors()
    return run_engine(plan, registry, history, period, Path(workdir), options or EngineOptions())[0]


def run_engine(
    plan: ScenarioPlan,
    registry: PluginRegistry,
    history: HistoryProvider,
    period: Period,
    workdir: Path,
    options: EngineOptions,
) -> tuple[EngineResult, _Engine]:
    """То же, что ``execute``, но возвращает и сам движок: превью читает из него результаты
    входов и число прочитанных строк."""
    engine = _Engine(plan, registry, history, period, workdir, options)
    return engine.run(), engine
