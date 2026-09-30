"""Разбор сценария без данных: плагины и их параметры, ссылки, столбцы, граф узлов.

Отсюда же — ``column_usage``: какие столбцы каких источников используют какие узлы. Он
нужен сверке структуры (``schema``): проверяются только реально используемые столбцы.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from autogenerator.contracts import (
    AgenError,
    AggregateSpec,
    AggregationPlugin,
    DatasetSpec,
    DType,
    ErrorCode,
    InputSpec,
    Issue,
    IssueLevel,
    MetricSpec,
    PeriodUnit,
    ScenarioSpec,
    StepPlugin,
    StepSpec,
    WindowPlugin,
    WindowSpec,
)
from autogenerator.contracts.yaml_io import validation_message
from autogenerator.plugin_host import PluginRegistry

from .sqlexpr import SqlTranslator

SERVICE_COLUMNS = ("_upload_id", "_upload_seq", "_row")


@dataclass
class InputSchema:
    """Что движку нужно знать об источнике входа до чтения данных."""

    period_column: str
    columns: dict[str, DType]


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


@dataclass
class InputPlan:
    spec: InputSpec
    schema: InputSchema
    steps: list[StepPlan] = field(default_factory=list)
    columns_after: list[str] = field(default_factory=list)

    @property
    def row_local(self) -> bool:
        return all(s.plugin.row_local for s in self.steps)


@dataclass
class DatasetPlan:
    spec: DatasetSpec
    window: WindowPlan | None
    aggregates: list[tuple[AggregateSpec, AggregationPlugin]] = field(default_factory=list)


@dataclass
class MetricPlan:
    spec: MetricSpec
    window: WindowPlan | None = None
    aggregation: AggregationPlugin | None = None
    refs: set[str] = field(default_factory=set)


@dataclass
class ScenarioPlan:
    scenario: ScenarioSpec
    inputs: dict[str, InputPlan] = field(default_factory=dict)
    datasets: dict[str, DatasetPlan] = field(default_factory=dict)
    metrics: dict[str, MetricPlan] = field(default_factory=dict)
    metric_order: list[str] = field(default_factory=list)
    usage: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]

    def raise_on_errors(self) -> None:
        if self.errors:
            text = "\n".join(f"  • {e}" for e in self.errors)
            raise AgenError(ErrorCode.SPEC_INVALID, f"Сценарий «{self.scenario.name}» с ошибками:\n{text}")

    def input_dependents(self, input_id: str) -> list[str]:
        nodes = [f"dataset:{d}" for d, p in self.datasets.items() if p.spec.input == input_id]
        nodes += [f"metric:{m}" for m, p in self.metrics.items() if p.spec.input == input_id]
        return nodes


class _Analyzer:
    def __init__(self, scenario: ScenarioSpec, registry: PluginRegistry, schemas: Mapping[str, InputSchema]):
        self.sc = scenario
        self.reg = registry
        self.schemas = schemas
        self.plan = ScenarioPlan(scenario=scenario)

    def error(self, node: str, message: str) -> None:
        self.plan.issues.append(Issue(level=IssueLevel.ERROR, node=node, message=message))

    def use(self, input_id: str, column: str, node: str) -> None:
        self.plan.usage.setdefault(input_id, {}).setdefault(column, [])
        if node not in self.plan.usage[input_id][column]:
            self.plan.usage[input_id][column].append(node)

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

    def columns(self, input_id: str, cols: set[str], node: str, where: str) -> None:
        """Проверить ссылки на столбцы входа и записать использование столбцов источника."""
        ip = self.plan.inputs.get(input_id)
        if ip is None:
            return
        available = set(ip.columns_after)
        source_cols = set(ip.schema.columns)
        produced = available - source_cols
        for c in sorted(cols):
            if c not in available:
                self.error(node, f"{where}: нет столбца «{c}» во входе «{input_id}»")
            elif c not in produced:
                self.use(input_id, c, node)

    # --- разделы сценария ----------------------------------------------------------

    def run(self) -> ScenarioPlan:
        for inp in self.sc.inputs:
            self.input(inp)
        for ds in self.sc.datasets:
            self.dataset(ds)
        for m in self.sc.metrics:
            self.metric(m)
        self.metric_order()
        return self.plan

    def input(self, inp: InputSpec) -> None:
        node = f"input:{inp.id}"
        schema = self.schemas.get(inp.id)
        if schema is None:
            self.error(node, f"не найден источник «{inp.source}»")
            return
        ip = InputPlan(spec=inp, schema=schema)
        self.plan.inputs[inp.id] = ip
        self.use(inp.id, schema.period_column, node)
        cols = [*schema.columns, *SERVICE_COLUMNS]
        source_cols = set(schema.columns)
        produced: set[str] = set()
        for st in inp.pipeline:
            snode = f"{node}/step:{st.id}"
            if not st.enabled:
                continue
            try:
                plugin = self.reg.step(st.type)
            except AgenError as e:
                self.error(snode, e.message)
                continue
            params = self.params(plugin, st.params, st.type_version, snode)
            if params is None:
                continue
            try:
                used = plugin.columns_used(params, SqlTranslator(cols))
            except AgenError as e:
                self.error(snode, e.message)
                used = set()
            for c in sorted(used):
                if c not in cols:
                    self.error(
                        snode,
                        f"нет столбца «{c}» (есть: {', '.join(x for x in cols if not x.startswith('_'))})",
                    )
                elif c in source_cols and c not in produced:
                    self.use(inp.id, c, snode)
            for inp_ref in plugin.inputs_used(params):
                if inp_ref not in {i.id for i in self.sc.inputs}:
                    self.error(snode, f"нет входа «{inp_ref}»")
            new_cols = plugin.output_columns(params, cols)
            produced |= set(new_cols) - set(cols)
            cols = new_cols
            ip.steps.append(StepPlan(st, plugin, params, snode))
        ip.columns_after = cols

    def dataset(self, ds: DatasetSpec) -> None:
        node = f"dataset:{ds.id}"
        if ds.input not in {i.id for i in self.sc.inputs}:
            self.error(node, f"нет входа «{ds.input}»")
            return
        wp = self.window(ds.window, node)
        plan = DatasetPlan(spec=ds, window=wp)
        self.plan.datasets[ds.id] = plan
        used: set[str] = set()
        if ds.where:
            try:
                used |= SqlTranslator().columns_in(ds.where)
            except AgenError as e:
                self.error(node, e.message)
        ip = self.plan.inputs.get(ds.input)
        for g in ds.group_by:
            used.add(g.column)
            if g.bucket is not None and ip is not None:
                dt = ip.schema.columns.get(g.column)
                if g.bucket == PeriodUnit.RANGE:
                    self.error(node, "группировка по произвольному диапазону не имеет смысла")
                elif dt is not None and dt not in (DType.DATE, DType.DATETIME):
                    self.error(
                        node,
                        f"группировать по {g.bucket} можно только столбец дат, а «{g.column}» — {dt}",
                    )
        outputs = [g.column for g in ds.group_by]
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
            outputs.append(a.output_name)
        if not ds.aggregate:
            used |= set(ds.columns)
            outputs = ds.columns or [c for c in (ip.columns_after if ip else []) if not c.startswith("_")]
        dupes = {o for o in outputs if outputs.count(o) > 1}
        if dupes:
            self.error(node, f"повторяются имена столбцов набора: {', '.join(sorted(dupes))}")
        for s in ds.sort or []:
            if s.column not in outputs:
                self.error(node, f"сортировка по «{s.column}», а в наборе есть: {', '.join(outputs)}")
        self.columns(ds.input, used, node, "набор")

    def metric(self, m: MetricSpec) -> None:
        node = f"metric:{m.id}"
        plan = MetricPlan(spec=m)
        self.plan.metrics[m.id] = plan
        if m.formula is not None:
            try:
                plan.refs = SqlTranslator().columns_in(m.formula)
            except AgenError as e:
                self.error(node, e.message)
                return
            known = {x.id for x in self.sc.metrics}
            for r in sorted(plan.refs - known):
                self.error(node, f"в формуле нет показателя «{r}»")
            return
        assert m.input is not None and m.fn is not None
        if m.input not in {i.id for i in self.sc.inputs}:
            self.error(node, f"нет входа «{m.input}»")
            return
        plan.window = self.window(m.window, node)
        try:
            plan.aggregation = self.reg.aggregation(m.fn)
        except AgenError as e:
            self.error(node, e.message)
        if plan.aggregation is not None and plan.aggregation.needs_column and not m.column:
            self.error(node, f"агрегату {m.fn} нужен столбец")
        used = {m.column} if m.column else set()
        if m.where:
            try:
                used |= SqlTranslator().columns_in(m.where)
            except AgenError as e:
                self.error(node, e.message)
        self.columns(m.input, used, node, "показатель")

    def metric_order(self) -> None:
        """Порядок расчёта показателей: формулы — после тех, на которые ссылаются."""
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(mid: str, path: list[str]) -> None:
            if state.get(mid) == 2 or mid not in self.plan.metrics:
                return
            if state.get(mid) == 1:
                cycle = " → ".join([*path[path.index(mid) :], mid])
                self.error(
                    f"metric:{mid}",
                    f"формулы показателей ссылаются друг на друга по кругу: {cycle}",
                )
                return
            state[mid] = 1
            for r in sorted(self.plan.metrics[mid].refs):
                visit(r, [*path, mid])
            state[mid] = 2
            order.append(mid)

        for m in self.sc.metrics:
            visit(m.id, [])
        self.plan.metric_order = order


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
