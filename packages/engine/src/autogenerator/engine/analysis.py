"""Разбор сценария без данных: плагины и их параметры, ссылки, столбцы и их типы, граф узлов.

Отсюда же — ``column_usage``: какие столбцы каких источников используют какие узлы. Он
нужен сверке структуры (``schema``): проверяются только реально используемые столбцы.

Типы столбцов отслеживаются от источника через все шаги: формулы и запросы SQL проверяет
DuckDB на пустых таблицах с теми же столбцами и типами, поэтому опечатка в имени столбца
или функции видна до чтения данных. Столбцы после кода на Python без объявленного
результата известны только после прогона: дальше по такому входу проверки мягче.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel, ValidationError

from autogenerator.contracts import (
    AgenError,
    AggregateSpec,
    AggregationPlugin,
    ColumnTypes,
    CompareSpec,
    DatasetSpec,
    DType,
    ErrorCode,
    InputSpec,
    Issue,
    IssueLevel,
    MetricSpec,
    PeriodUnit,
    PluginKind,
    ScenarioSpec,
    SourceSpec,
    StepPlugin,
    StepSpec,
    WindowPlugin,
    WindowSpec,
)
from autogenerator.contracts.yaml_io import validation_message
from autogenerator.plugin_host import PluginRegistry

from .duck import Duck
from .sqlexpr import SqlTranslator, query_columns, tables_in
from .usercode import UserCodeError, check_code, string_literals

SERVICE_COLUMNS = ("_upload_id", "_upload_seq", "_row")
SERVICE_TYPES: ColumnTypes = {"_upload_id": DType.STRING, "_upload_seq": DType.INT, "_row": DType.INT}

POLARS_TYPES: dict[DType, pl.DataType] = {
    DType.STRING: pl.String(),
    DType.INT: pl.Int64(),
    DType.FLOAT: pl.Float64(),
    DType.DATE: pl.Date(),
    DType.DATETIME: pl.Datetime("us"),
    DType.BOOL: pl.Boolean(),
}


def dtype_of_polars(dt: Any) -> DType | None:
    if dt == pl.Date:
        return DType.DATE
    if isinstance(dt, pl.Datetime):
        return DType.DATETIME
    if dt == pl.Boolean:
        return DType.BOOL
    if dt.is_integer():
        return DType.INT
    if dt.is_float() or dt.is_decimal():
        return DType.FLOAT
    if dt in (pl.String, pl.Categorical) or isinstance(dt, pl.Enum):
        return DType.STRING
    return None


@dataclass
class InputSchema:
    """Что движку нужно знать об источнике входа до чтения данных."""

    period_column: str
    columns: dict[str, DType]
    period_unit: PeriodUnit = PeriodUnit.MONTH
    keys: list[str] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    """id столбца → название в выгрузке (для ``ctx.columns`` пользовательского кода)."""

    @classmethod
    def from_source(cls, spec: SourceSpec) -> InputSchema:
        return cls(
            period_column=spec.period_column,
            columns=spec.dtypes,
            period_unit=spec.period_type,
            keys=list(spec.keys),
            names={c.id: c.name for c in spec.columns},
        )


@dataclass
class WindowPlan:
    spec: WindowSpec
    plugin: WindowPlugin
    params: BaseModel


@dataclass
class StepPlan:
    spec: StepSpec
    plugin: StepPlugin
    params: BaseModel
    node: str
    inputs: list[str] = field(default_factory=list)
    lookback: int | None = 0
    keys: dict[str, list[str]] = field(default_factory=dict)
    cacheable: bool = True


@dataclass
class InputPlan:
    spec: InputSpec
    schema: InputSchema
    steps: list[StepPlan] = field(default_factory=list)
    schema_after: ColumnTypes | None = None
    """Столбцы и типы после обработки; ``None`` — известны только после прогона."""
    deps: list[str] = field(default_factory=list)
    """Другие входы, которые нужны шагам (объединение, SQL)."""
    needed: set[str] | None = field(default_factory=set)
    """Столбцы, которые нужны дальше (наборам, показателям); ``None`` — все."""
    source_columns: set[str] | None = None
    """Столбцы, которые читаются из истории; ``None`` — все."""
    produced: set[str] = field(default_factory=set)
    """Столбцы, которые шаги добавили или записали заново: в них уже не данные источника."""
    reads_all: bool = False
    """Какому-то шагу нужны все столбцы входа."""

    @property
    def columns_after(self) -> list[str]:
        return list(self.schema_after or {})

    @property
    def row_local(self) -> bool:
        return all(s.lookback == 0 for s in self.steps)

    @property
    def lookback(self) -> int | None:
        """Сколько единиц периода истории до нижней границы окон нужно шагам."""
        total = 0
        for s in self.steps:
            if s.lookback is None:
                return None
            total = max(total, s.lookback)
        return total


@dataclass
class DatasetPlan:
    spec: DatasetSpec
    window: WindowPlan | None
    aggregates: list[tuple[AggregateSpec, AggregationPlugin]] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    """Узлы, от которых зависит набор: input:…, dataset:…"""
    tables: list[str] = field(default_factory=list)
    """id входов и наборов, которые читает запрос или код."""
    output: ColumnTypes | None = None


@dataclass
class MetricPlan:
    spec: MetricSpec
    window: WindowPlan | None = None
    aggregation: AggregationPlugin | None = None
    refs: set[str] = field(default_factory=set)
    tables: list[str] = field(default_factory=list)
    base: str | None = None
    """Показатель сравнения: id показателя, который считается за другой период …"""
    shift: str | None = None
    """… previous_period или same_period_last_year; или разница с ним: change, change_pct."""
    change: str | None = None

    @property
    def deps(self) -> list[str]:
        if self.base is not None:
            return [f"metric:{self.base}"] + ([f"metric:{r}" for r in sorted(self.refs)] if self.change else [])
        kind = self.spec.kind
        if kind == "input":
            return [f"input:{self.spec.input}"]
        if kind == "dataset":
            return [f"dataset:{self.spec.dataset}"]
        if kind == "formula":
            return [f"metric:{r}" for r in sorted(self.refs)]
        return [f"input:{t}" if not t.startswith("dataset:") else t for t in self.tables]


@dataclass
class ScenarioPlan:
    scenario: ScenarioSpec
    inputs: dict[str, InputPlan] = field(default_factory=dict)
    input_order: list[str] = field(default_factory=list)
    datasets: dict[str, DatasetPlan] = field(default_factory=dict)
    dataset_order: list[str] = field(default_factory=list)
    metrics: dict[str, MetricPlan] = field(default_factory=dict)
    metric_order: list[str] = field(default_factory=list)
    usage: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    mentioned: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    """Слабая связь: столбцы, найденные в тексте кода на Python (вход → столбец → узлы)."""
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]

    def raise_on_errors(self) -> None:
        if self.errors:
            text = "\n".join(f"  • {e}" for e in self.errors)
            raise AgenError(ErrorCode.SPEC_INVALID, f"Сценарий «{self.scenario.name}» с ошибками:\n{text}")

    def input_dependents(self, input_id: str) -> list[str]:
        nodes = [f"dataset:{d}" for d, p in self.datasets.items() if f"input:{input_id}" in p.deps]
        nodes += [f"metric:{m}" for m, p in self.metrics.items() if f"input:{input_id}" in p.deps]
        return nodes

    def table_ids(self) -> set[str]:
        return {i.id for i in self.scenario.inputs} | {d.id for d in self.scenario.datasets}

    def dependencies(self, node: str) -> list[str]:
        """Узлы, от которых зависит узел (input:…, dataset:…, metric:…)."""
        kind, nid = node.split(":", 1)
        if kind == "input":
            ip = self.inputs.get(nid)
            return [f"input:{d}" for d in ip.deps] if ip else []
        if kind == "dataset":
            dp = self.datasets.get(nid)
            return list(dp.deps) if dp else []
        mp = self.metrics.get(nid)
        return mp.deps if mp else []

    def closure(self, nodes: list[str]) -> set[str]:
        """Узлы вместе со всем, от чего они зависят."""
        seen: set[str] = set()
        stack = list(nodes)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.dependencies(n))
        return seen


class _Tools:
    """Реализация ``SchemaTools`` для разбора сценария."""

    def __init__(self, analyzer: _Analyzer, schema: ColumnTypes | None):
        self._an = analyzer
        self._schema = schema
        types = {k: POLARS_TYPES[v] for k, v in (schema or {}).items() if v is not None}
        self._tr = SqlTranslator(types if schema is not None else None)
        if schema is not None:
            self._tr.columns = set(schema)

    def expr(self, sql: str) -> pl.Expr:
        return self._tr.expr(sql)

    def columns_in(self, sql: str) -> set[str]:
        return self._tr.columns_in(sql)

    def tables_in(self, sql: str) -> set[str]:
        return tables_in(sql)

    def query_columns(self, sql: str, table: str) -> set[str] | None:
        tables = self._an.table_columns()
        if self._schema is not None:
            tables["data"] = list(self._schema)
        return query_columns(sql, table, tables)

    def expr_type(self, sql: str, schema: ColumnTypes) -> DType | None:
        cols = self._tr.columns_in(sql)
        missing = sorted(c for c in cols if c not in schema)
        if missing:
            known = ", ".join(sorted(c for c in schema if not c.startswith("_")))
            raise AgenError(ErrorCode.EXPRESSION, f"В формуле «{sql}» нет столбца «{missing[0]}». Есть: {known}")
        if any(schema.get(c) is None for c in cols):
            return None
        return self._an.duck.expr_type(sql, schema)

    def query_schema(self, sql: str, tables: Mapping[str, ColumnTypes]) -> ColumnTypes | None:
        all_tables: dict[str, ColumnTypes] = {}
        for t in tables_in(sql):
            if t in tables:
                continue
            other = self._an.table_schema(t)
            if other is None:
                return None  # таблица станет известна только после прогона
            all_tables[t] = other
        all_tables.update(tables)
        if any(v is None for cols in all_tables.values() for v in cols.values()):
            try:
                return self._an.duck.describe(sql, all_tables)
            except AgenError:
                return None  # без типов DuckDB мог ошибиться: проверим при прогоне
        return self._an.duck.describe(sql, all_tables)

    def input_schema(self, input_id: str) -> ColumnTypes | None:
        ip = self._an.plan.inputs.get(input_id)
        return dict(ip.schema_after) if ip is not None and ip.schema_after is not None else None


class _Analyzer:
    def __init__(self, scenario: ScenarioSpec, registry: PluginRegistry, schemas: Mapping[str, InputSchema]):
        self.sc = scenario
        self.reg = registry
        self.schemas = schemas
        self.plan = ScenarioPlan(scenario=scenario)
        self.duck = Duck(Path("."))
        self.cycles: set[tuple[str, str]] = set()

    def error(self, node: str, message: str) -> None:
        self.plan.issues.append(Issue(level=IssueLevel.ERROR, node=node, message=message))

    def warning(self, node: str, message: str) -> None:
        self.plan.issues.append(Issue(level=IssueLevel.WARNING, node=node, message=message))

    def use(self, input_id: str, column: str, node: str, weak: bool = False) -> None:
        target = self.plan.mentioned if weak else self.plan.usage
        target.setdefault(input_id, {}).setdefault(column, [])
        if node not in target[input_id][column]:
            target[input_id][column].append(node)

    def params(self, plugin: Any, raw: dict[str, Any], version: int, node: str) -> BaseModel | None:
        try:
            parsed: BaseModel = plugin.parse_params(raw, version)
            return parsed
        except ValidationError as e:
            self.error(node, validation_message(e, f"параметры «{plugin.name}»"))
        except ValueError as e:
            self.error(node, str(e))
        return None

    def window(self, spec: WindowSpec, node: str) -> WindowPlan | None:
        try:
            plugin = self.reg.window(spec.type)
        except AgenError as e:
            self.error(node, f"окно: {e.message}")
            return None
        params = self.params(plugin, spec.params, 1, node)
        return WindowPlan(spec, plugin, params) if params is not None else None

    def table_schema(self, table: str) -> ColumnTypes | None:
        if table in self.plan.inputs:
            return self.plan.inputs[table].schema_after
        if table in self.plan.datasets:
            return self.plan.datasets[table].output
        return None

    def table_columns(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self.plan.table_ids():
            sch = self.table_schema(t)
            if sch is not None:
                out[t] = list(sch)
        return out

    def columns(self, input_id: str, cols: set[str] | None, node: str, where: str) -> None:
        """Проверить ссылки на столбцы входа и записать использование столбцов источника.
        ``cols=None`` — нужны все столбцы."""
        ip = self.plan.inputs.get(input_id)
        if ip is None:
            return
        if cols is None:
            ip.needed = None
            return
        if ip.needed is not None:
            ip.needed |= cols
        if ip.schema_after is None:
            for c in sorted((cols & set(ip.schema.columns)) - ip.produced):
                self.use(input_id, c, node)
            return
        available = set(ip.schema_after)
        produced = ip.produced
        for c in sorted(cols):
            if c not in available:
                known = ", ".join(x for x in ip.schema_after if not x.startswith("_"))
                self.error(node, f"{where}: нет столбца «{c}» во входе «{input_id}» (есть: {known})")
            elif c not in produced:
                self.use(input_id, c, node)

    # --- разделы сценария ----------------------------------------------------------

    def run(self) -> ScenarioPlan:
        order = self.input_order()
        for iid in order:
            self.input(self.sc.input(iid))
        self.plan.input_order = [i for i in order if i in self.plan.inputs]
        for did in self.dataset_order():
            self.dataset(next(d for d in self.sc.datasets if d.id == did))
        self.plan.dataset_order = [d for d in self.dataset_order() if d in self.plan.datasets]
        for m in self.sc.metrics:
            self.metric(m)
        self.metric_order()
        self.source_columns()
        return self.plan

    def input_refs(self, inp: InputSpec) -> list[str]:
        """Другие входы, на которые ссылаются шаги входа (без проверки параметров)."""
        refs: list[str] = []
        for st in inp.pipeline:
            if not st.enabled or not self.reg.has(PluginKind.STEP, st.type):
                continue
            plugin = self.reg.step(st.type)
            try:
                params = plugin.parse_params(st.params, st.type_version)
                refs += plugin.inputs_used(params, SqlTranslator())
            except Exception:
                continue
        return refs

    def input_order(self) -> list[str]:
        ids = [i.id for i in self.sc.inputs]
        deps = {i.id: [r for r in self.input_refs(i) if r in ids and r != i.id] for i in self.sc.inputs}
        return self.topo(ids, deps, "input", "входы ссылаются друг на друга по кругу")

    def dataset_order(self) -> list[str]:
        ids = [d.id for d in self.sc.datasets]
        deps: dict[str, list[str]] = {}
        for d in self.sc.datasets:
            refs: list[str] = []
            if d.type == "sql" and d.query:
                try:
                    refs = sorted(tables_in(d.query))
                except AgenError:
                    refs = []
            elif d.type == "python":
                refs = list(d.inputs)
            deps[d.id] = [r for r in refs if r in ids and r != d.id]
        return self.topo(ids, deps, "dataset", "наборы ссылаются друг на друга по кругу")

    def topo(self, ids: list[str], deps: dict[str, list[str]], kind: str, what: str) -> list[str]:
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(x: str, path: list[str]) -> None:
            if state.get(x) == 2:
                return
            if state.get(x) == 1:
                cycle = " → ".join([*path[path.index(x) :], x])
                if (kind, x) not in self.cycles:
                    self.cycles.add((kind, x))
                    self.error(f"{kind}:{x}", f"{what}: {cycle}")
                return
            state[x] = 1
            for d in deps.get(x, []):
                visit(d, [*path, x])
            state[x] = 2
            order.append(x)

        for x in ids:
            visit(x, [])
        return order

    def input(self, inp: InputSpec) -> None:
        node = f"input:{inp.id}"
        schema = self.schemas.get(inp.id)
        if schema is None:
            self.error(node, f"не найден источник «{inp.source}»")
            return
        ip = InputPlan(spec=inp, schema=schema)
        self.plan.inputs[inp.id] = ip
        self.use(inp.id, schema.period_column, node)
        current: ColumnTypes | None = {**schema.columns, **SERVICE_TYPES}
        source_cols = set(schema.columns)
        produced = ip.produced
        input_ids = {i.id for i in self.sc.inputs}
        for st in inp.pipeline:
            snode = f"{node}/step:{st.id}"
            if not st.enabled:
                continue
            try:
                plugin = self.reg.step(st.type)
            except AgenError as e:
                self.error(snode, e.message)
                current = None
                continue
            params = self.params(plugin, st.params, st.type_version, snode)
            if params is None:
                current = None
                continue
            tools = _Tools(self, current)
            sp = StepPlan(st, plugin, params, snode)
            try:
                for issue in plugin.check(params, tools):
                    self.plan.issues.append(issue.model_copy(update={"node": snode}))
                used = plugin.columns_used(params, tools)
                sp.inputs = plugin.inputs_used(params, tools)
                sp.lookback = plugin.lookback(params)
                sp.keys = plugin.key_columns(params)
                sp.cacheable = plugin.cacheable(params)
                mentioned = plugin.columns_mentioned(params)
                reads_all = plugin.reads_all_columns(params, tools)
                written = plugin.columns_written(params)
            except (AgenError, UserCodeError) as e:
                self.error(snode, e.message)
                current = None
                ip.steps.append(sp)
                continue
            for other in sp.inputs:
                if other not in input_ids:
                    self.error(snode, f"нет входа «{other}»")
                elif other == inp.id:
                    self.error(snode, "вход не может ссылаться сам на себя")
                elif other not in ip.deps:
                    ip.deps.append(other)
            if current is not None:
                for c in sorted(used):
                    if c not in current:
                        known = ", ".join(x for x in current if not x.startswith("_"))
                        self.error(snode, f"нет столбца «{c}» (есть: {known})")
                    elif c in source_cols and c not in produced:
                        self.use(inp.id, c, snode)
                for c in sorted(mentioned & set(current)):
                    if c in source_cols and c not in produced:
                        self.use(inp.id, c, snode, weak=True)
            else:
                for c in sorted((used & source_cols) - produced):
                    self.use(inp.id, c, snode)
            ip.reads_all = ip.reads_all or reads_all
            if current is not None:
                try:
                    after = plugin.output_schema(params, dict(current), tools)
                except AgenError as e:
                    self.error(snode, e.message)
                    after = None
                if after is not None:
                    produced |= set(after) - set(current)
                current = after
            # Формула с id столбца источника заменяет его: дальше по сценарию это результат шага.
            produced |= written
            ip.steps.append(sp)
        ip.schema_after = current

    # --- наборы данных ------------------------------------------------------------

    def dataset(self, ds: DatasetSpec) -> None:
        node = f"dataset:{ds.id}"
        wp = self.window(ds.window, node)
        plan = DatasetPlan(spec=ds, window=wp)
        self.plan.datasets[ds.id] = plan
        if ds.type == "sql":
            self.dataset_sql(ds, plan, node)
        elif ds.type == "python":
            self.dataset_python(ds, plan, node)
        else:
            self.dataset_table(ds, plan, node)

    def table_refs(self, refs: list[str], node: str, plan: DatasetPlan | MetricPlan) -> None:
        tables = self.plan.table_ids()
        for r in refs:
            if r not in tables:
                self.error(node, f"нет входа или набора «{r}»")
                continue
            if r in self.plan.inputs or r in {i.id for i in self.sc.inputs}:
                plan.tables.append(r)
                if isinstance(plan, DatasetPlan):
                    plan.deps.append(f"input:{r}")
            else:
                plan.tables.append(f"dataset:{r}")
                if isinstance(plan, DatasetPlan):
                    plan.deps.append(f"dataset:{r}")

    def dataset_sql(self, ds: DatasetSpec, plan: DatasetPlan, node: str) -> None:
        assert ds.query is not None
        try:
            refs = sorted(tables_in(ds.query))
        except AgenError as e:
            self.error(node, e.message)
            return
        self.table_refs(refs, node, plan)
        for t in refs:
            if t in self.plan.inputs:
                self.columns(t, query_columns(ds.query, t, self.table_columns()), node, "запрос")
        tables: dict[str, ColumnTypes] = {}
        for t in refs:
            sch = self.table_schema(t)
            if sch is None:
                return  # проверим при прогоне
            tables[t] = sch
        try:
            plan.output = self.duck.describe(ds.query, tables)
        except AgenError as e:
            if not any(v is None for cols in tables.values() for v in cols.values()):
                self.error(node, e.message)

    def dataset_python(self, ds: DatasetSpec, plan: DatasetPlan, node: str) -> None:
        assert ds.code is not None
        try:
            for msg in check_code(ds.code, "build", 2, node):
                self.error(node, msg)
        except UserCodeError as e:
            self.error(node, e.message)
        self.table_refs(list(ds.inputs), node, plan)
        literals = string_literals(ds.code)
        for t in ds.inputs:
            if t in self.plan.inputs:
                self.columns(t, None, node, "код")
                for c in sorted(literals & set(self.plan.inputs[t].schema.columns)):
                    self.use(t, c, node, weak=True)

    def dataset_table(self, ds: DatasetSpec, plan: DatasetPlan, node: str) -> None:
        assert ds.input is not None
        if ds.input not in {i.id for i in self.sc.inputs}:
            self.error(node, f"нет входа «{ds.input}»")
            return
        plan.deps.append(f"input:{ds.input}")
        ip = self.plan.inputs.get(ds.input)
        schema = ip.schema_after if ip is not None else None
        used: set[str] = set()
        if ds.where:
            try:
                used |= SqlTranslator().columns_in(ds.where)
                if schema is not None:
                    _Tools(self, schema).expr_type(ds.where, schema)
            except AgenError as e:
                self.error(node, e.message)
        out: ColumnTypes = {}
        for g in ds.group_by:
            used.add(g.column)
            dt = schema.get(g.column) if schema is not None else None
            if g.bucket is not None:
                if g.bucket == PeriodUnit.RANGE:
                    self.error(node, "группировка по произвольному диапазону не имеет смысла")
                elif dt is not None and dt not in (DType.DATE, DType.DATETIME):
                    self.error(
                        node,
                        f"группировать по {g.bucket} можно только столбец дат, а «{g.column}» — {dt}",
                    )
                out[g.column] = DType.DATE
            else:
                out[g.column] = dt
        for a in ds.aggregate:
            try:
                agg = self.reg.aggregation(a.fn)
            except AgenError as e:
                self.error(node, e.message)
                continue
            if agg.needs_column and not a.column:
                self.error(node, f"агрегату {a.fn} нужен столбец")
            if a.column:
                used.add(a.column)
            plan.aggregates.append((a, agg))
            out[a.output_name] = _agg_type(a, agg, schema)
        if not ds.aggregate:
            if ds.columns:
                used |= set(ds.columns)
                out = {c: (schema or {}).get(c) for c in ds.columns}
            else:
                out = {c: t for c, t in (schema or {}).items() if not c.startswith("_")}
                self.columns(ds.input, None, node, "набор")
        outputs = [g.column for g in ds.group_by] + [a.output_name for a in ds.aggregate] if ds.aggregate else list(out)
        for c in ds.compare:
            for a in ds.aggregate:
                base = f"{a.output_name}_{c.tag}"
                for name, t in (
                    (base, out.get(a.output_name)),
                    (f"{base}_change", out.get(a.output_name)),
                    (f"{base}_change_pct", DType.FLOAT),
                ):
                    outputs.append(name)
                    out[name] = t
        if ds.compare and plan.window is not None and not ds.aggregate:
            self.error(node, "сравнение периодов работает только с aggregate")
        for d in ds.derive:
            if d.fn == "formula":
                assert d.expr is not None
                try:
                    cols = SqlTranslator().columns_in(d.expr)
                except AgenError as e:
                    self.error(node, e.message)
                    continue
                missing = sorted(cols - set(outputs))
                if missing:
                    self.error(node, f"в формуле «{d.expr}» нет столбца «{missing[0]}» (есть: {', '.join(outputs)})")
                    continue
                try:
                    out[d.output_name] = self.duck.expr_type(d.expr, {k: v for k, v in out.items() if k in cols})
                except AgenError as e:
                    if all(out.get(k) is not None for k in cols):
                        self.error(node, e.message)
                    out[d.output_name] = None
            else:
                if d.column not in outputs:
                    self.error(node, f"расчёт {d.fn}: нет столбца «{d.column}» (есть: {', '.join(outputs)})")
                out[d.output_name] = DType.INT if d.fn == "rank" else DType.FLOAT
            outputs.append(d.output_name)
        if ds.pivot is not None:
            out = {g.column: out.get(g.column) for g in ds.group_by if g.column != ds.pivot}
            plan.output = None  # столбцы сводной — значения данных
        dupes = {o for o in outputs if outputs.count(o) > 1}
        if dupes:
            self.error(node, f"повторяются имена столбцов набора: {', '.join(sorted(dupes))}")
        for s in ds.sort or []:
            if ds.pivot is not None:
                if s.column not in out:
                    self.error(node, f"сводную таблицу можно сортировать по столбцам строк: {', '.join(out)}")
            elif s.column not in outputs:
                self.error(node, f"сортировка по «{s.column}», а в наборе есть: {', '.join(outputs)}")
        if ds.pivot is None:
            plan.output = out
        self.columns(ds.input, used, node, "набор")

    # --- показатели ---------------------------------------------------------------

    def metric(self, m: MetricSpec) -> None:
        node = f"metric:{m.id}"
        plan = MetricPlan(spec=m)
        self.plan.metrics[m.id] = plan
        kind = m.kind
        if kind == "formula":
            assert m.formula is not None
            try:
                plan.refs = SqlTranslator().columns_in(m.formula)
                SqlTranslator().expr(m.formula)
            except AgenError as e:
                if not e.details.get("unsupported"):
                    self.error(node, e.message)
                    return
            known = {x.id for x in self.sc.metrics} | {x for mm in self.sc.metrics for x in mm.compare_ids()}
            for r in sorted(plan.refs - known):
                self.error(node, f"в формуле нет показателя «{r}»")
        elif kind == "input":
            self.metric_input(m, plan, node)
        elif kind == "dataset":
            self.metric_dataset(m, plan, node)
        elif kind == "sql":
            self.metric_sql(m, plan, node)
        else:
            self.metric_python(m, plan, node)
        if m.compare and kind == "dataset":
            self.error(node, "сравнение периодов у показателя из набора не считается: задайте compare у набора")
        for c in m.compare:
            self.compare_metrics(m, c)

    def compare_metrics(self, m: MetricSpec, c: CompareSpec) -> None:
        base = f"{m.id}_{c.tag}"
        shifted = MetricSpec(id=base, formula="0")
        self.plan.metrics[base] = MetricPlan(spec=shifted, base=m.id, shift=c.window)
        for suffix in ("change", "change_pct"):
            mid = f"{base}_{suffix}"
            spec = MetricSpec(id=mid, formula="0")
            self.plan.metrics[mid] = MetricPlan(spec=spec, base=m.id, refs={base}, change=suffix)

    def metric_input(self, m: MetricSpec, plan: MetricPlan, node: str) -> None:
        assert m.input is not None and m.fn is not None
        if m.input not in {i.id for i in self.sc.inputs}:
            self.error(node, f"нет входа «{m.input}»")
            return
        plan.window = self.window(m.window, node)
        plan.aggregation = self.aggregation(m, node)
        used = {m.column} if m.column else set()
        if m.where:
            try:
                used |= SqlTranslator().columns_in(m.where)
                ip = self.plan.inputs.get(m.input)
                if ip is not None and ip.schema_after is not None:
                    _Tools(self, ip.schema_after).expr_type(m.where, ip.schema_after)
            except AgenError as e:
                self.error(node, e.message)
        self.columns(m.input, used, node, "показатель")

    def aggregation(self, m: MetricSpec, node: str) -> AggregationPlugin | None:
        assert m.fn is not None
        try:
            agg = self.reg.aggregation(m.fn)
        except AgenError as e:
            self.error(node, e.message)
            return None
        if agg.needs_column and not m.column:
            self.error(node, f"агрегату {m.fn} нужен столбец")
        return agg

    def metric_dataset(self, m: MetricSpec, plan: MetricPlan, node: str) -> None:
        assert m.dataset is not None
        if m.dataset not in self.plan.datasets:
            self.error(node, f"нет набора «{m.dataset}»")
            return
        plan.aggregation = self.aggregation(m, node)
        out = self.plan.datasets[m.dataset].output
        cols = ({m.column} if m.column else set()) | (SqlTranslator().columns_in(m.where) if m.where else set())
        if out is not None:
            for c in sorted(cols - set(out)):
                self.error(node, f"нет столбца «{c}» в наборе «{m.dataset}» (есть: {', '.join(out)})")

    def metric_sql(self, m: MetricSpec, plan: MetricPlan, node: str) -> None:
        assert m.query is not None
        plan.window = self.window(m.window, node)
        try:
            refs = sorted(tables_in(m.query))
        except AgenError as e:
            self.error(node, e.message)
            return
        self.table_refs(refs, node, plan)
        for t in refs:
            if t in self.plan.inputs:
                self.columns(t, query_columns(m.query, t, self.table_columns()), node, "запрос")
        tables = {t: self.table_schema(t) for t in refs}
        if all(v is not None for v in tables.values()):
            try:
                res = self.duck.describe(m.query, {k: v for k, v in tables.items() if v is not None})
            except AgenError as e:
                self.error(node, e.message)
                return
            if len(res) != 1:
                self.error(node, f"запрос показателя должен вернуть один столбец, а возвращает {len(res)}")

    def metric_python(self, m: MetricSpec, plan: MetricPlan, node: str) -> None:
        assert m.code is not None
        plan.window = self.window(m.window, node)
        try:
            for msg in check_code(m.code, "value", 2, node):
                self.error(node, msg)
        except UserCodeError as e:
            self.error(node, e.message)
        self.table_refs(list(m.inputs), node, plan)
        literals = string_literals(m.code)
        for t in m.inputs:
            if t in self.plan.inputs:
                self.columns(t, None, node, "код")
                for c in sorted(literals & set(self.plan.inputs[t].schema.columns)):
                    self.use(t, c, node, weak=True)

    def metric_order(self) -> None:
        """Порядок расчёта показателей: формулы и сравнения — после тех, на которые ссылаются."""
        ids = list(self.plan.metrics)
        deps = {
            mid: [d.split(":", 1)[1] for d in p.deps if d.startswith("metric:")] for mid, p in self.plan.metrics.items()
        }
        # Сравнение формулы пересчитывает формулу за другой период: нужны её показатели.
        for mid, p in self.plan.metrics.items():
            if p.base is not None and p.shift is not None:
                deps[mid] = [p.base]
        self.plan.metric_order = self.topo(ids, deps, "metric", "формулы показателей ссылаются друг на друга по кругу")

    # --- какие столбцы читать ----------------------------------------------------------

    def source_columns(self) -> None:
        """Столбцы, которые нужно прочитать из истории каждого входа: используемые шагами и
        нужные дальше. ``None`` — все (код без ``uses``, ``SELECT *``, набор без столбцов)."""
        for iid, ip in self.plan.inputs.items():
            needed_by_others = any(iid in other.deps for other in self.plan.inputs.values())
            if needed_by_others:
                ip.needed = None
            if ip.needed is None or ip.reads_all or ip.schema_after is None:
                ip.source_columns = None
                continue
            used = set(self.plan.usage.get(iid, {})) | set(self.plan.mentioned.get(iid, {}))
            need = used | (ip.needed & set(ip.schema.columns)) | {ip.schema.period_column}
            ip.source_columns = need


def _agg_type(a: AggregateSpec, agg: AggregationPlugin, schema: ColumnTypes | None) -> DType | None:
    if a.fn in ("count", "count_distinct"):
        return DType.INT
    if a.fn in ("mean", "median"):
        return DType.FLOAT
    if a.column and schema is not None:
        t = schema.get(a.column)
        if a.fn == "sum" and t == DType.BOOL:
            return DType.INT
        return t
    return None


def analyze(scenario: ScenarioSpec, registry: PluginRegistry, schemas: Mapping[str, InputSchema]) -> ScenarioPlan:
    """Разобрать сценарий: плагины, параметры, ссылки, столбцы, порядок узлов.

    ``schemas`` — схема источника для каждого входа сценария (по ``id`` входа).
    """
    return _Analyzer(scenario, registry, schemas).run()


def column_usage(
    scenario: ScenarioSpec, registry: PluginRegistry, schemas: Mapping[str, InputSchema]
) -> dict[str, dict[str, list[str]]]:
    """Вход → столбец источника → узлы, которые его используют."""
    return analyze(scenario, registry, schemas).usage
