"""Что шаг получает при выполнении: реализация ``StepContext`` из контрактов.

Через контекст шаг переводит формулы (Polars, а если формулу не перевести — DuckDB),
отдаёт тяжёлые операции DuckDB (``sql``), читает другие входы сценария и запускает
пользовательский код в отдельном процессе.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import pyarrow.parquet as pq

from autogenerator.contracts import (
    AgenError,
    CodeFrame,
    CodeMode,
    DateSpan,
    ErrorCode,
    PandasTypes,
    Period,
    PluginKind,
    WindowSpec,
)

from .cache import make_key
from .duck import quote
from .isolated import run_job
from .resources import gb
from .sqlexpr import SqlTranslator, UnsupportedExpression, query_columns, tables_in
from .usercode import UserContext, run_lazy

if TYPE_CHECKING:
    from .analysis import InputPlan, StepPlan
    from .execute import _Engine

STANDARD_WINDOWS = (
    "report_period",
    "previous_period",
    "same_period_last_year",
    "quarter_to_date",
    "year_to_date",
    "all",
)
_counter = itertools.count()


def user_context(engine: _Engine, node: str, period: Period, columns: dict[str, str] | None = None) -> UserContext:
    """``ctx`` пользовательского кода: период, точка отсчёта, стандартные окна."""
    windows: dict[str, DateSpan] = {}
    for name in STANDARD_WINDOWS:
        if engine.registry.has(PluginKind.WINDOW, name):
            windows[name] = engine.resolve_window(WindowSpec(type=name), period)
    return UserContext(
        period=period,
        anchor=engine.anchor,
        windows=windows,
        columns=columns or {},
        node=node,
        resolver=lambda spec: engine.resolve_window(WindowSpec.model_validate(spec), period),
    )


def context_json(ctx: UserContext) -> dict[str, Any]:
    return {
        "period": ctx.period.model_dump(mode="json"),
        "anchor": ctx.anchor.isoformat(),
        "windows": {k: v.model_dump(mode="json") for k, v in ctx.windows.items()},
        "columns": ctx.columns,
        "node": ctx.node,
    }


class StepRunContext:
    """Реализация ``StepContext`` для одного шага одного входа."""

    def __init__(self, engine: _Engine, ip: InputPlan, sp: StepPlan, lf: pl.LazyFrame, prefix_key: str | None):
        self.input_id = ip.spec.id
        self.step_id = sp.spec.id
        self.period = engine.period
        self.period_column = ip.schema.period_column
        self.anchor: date = engine.anchor
        self.large = engine.large.get(ip.spec.id, False)
        self.preview = engine.opts.preview
        self.node = sp.node
        self._engine = engine
        self._ip = ip
        self._prefix_key = prefix_key
        self._calls = 0
        self._tr = SqlTranslator(dict(lf.collect_schema()))

    # --- выражения -------------------------------------------------------------

    def expr(self, sql: str) -> pl.Expr:
        return self._tr.expr(sql)

    def columns_in(self, sql: str) -> set[str]:
        return self._tr.columns_in(sql)

    def tables_in(self, sql: str) -> set[str]:
        return tables_in(sql)

    def query_columns(self, sql: str, table: str) -> set[str] | None:
        return query_columns(sql, table)

    def window(self, spec: WindowSpec | str) -> DateSpan:
        ws = spec if isinstance(spec, WindowSpec) else WindowSpec.model_validate(spec)
        return self._engine.resolve_window(ws, self.period)

    def warn(self, message: str) -> None:
        self._engine.warn(self.node, message)

    def log(self, text: str) -> None:
        self._engine.info(self.node, text)

    def with_column(self, lf: pl.LazyFrame, name: str, sql: str) -> pl.LazyFrame:
        tr = SqlTranslator(dict(lf.collect_schema()))
        try:
            return lf.with_columns(tr.expr(sql).alias(name))
        except UnsupportedExpression:
            pass
        names = lf.collect_schema().names()
        if name in names:
            query = f"SELECT * REPLACE (({sql}) AS {quote(name)}) FROM data"
        else:
            query = f"SELECT *, ({sql}) AS {quote(name)} FROM data"
        return self.sql(query, {"data": lf})

    def filter(self, lf: pl.LazyFrame, sql: str) -> pl.LazyFrame:
        tr = SqlTranslator(dict(lf.collect_schema()))
        try:
            # Пустой результат условия (NULL) строку не пропускает, как в SQL.
            return lf.filter(tr.expr(sql).fill_null(False))
        except UnsupportedExpression:
            pass
        return self.sql(f"SELECT * FROM data WHERE ({sql})", {"data": lf})

    def input(self, input_id: str) -> pl.LazyFrame:
        return self._engine.input_frame(input_id, self.node)

    # --- материализация ---------------------------------------------------------

    def _cached(self, what: str) -> tuple[str | None, Path | None]:
        """Ключ промежуточного результата шага в кэше и файл, если он уже есть. Записи журнала
        и предупреждения, сохранённые с результатом, повторяются в журнале этого запуска."""
        self._calls += 1
        cache = self._engine.opts.cache
        if self._prefix_key is None or cache is None:
            return None, None
        key = make_key("step", self._prefix_key, what, self._calls)
        hit = cache.get(key)
        if hit is None:
            return key, None
        path, info = hit
        for line in info.get("logs") or []:
            self.log(line)
        for w in info.get("warnings") or []:
            self.warn(w)
        return key, path

    def _store(self, key: str | None, path: Path, info: dict[str, Any] | None = None) -> Path:
        cache = self._engine.opts.cache
        if key is None or cache is None:
            return path
        return cache.put_file(key, path, info)

    def sql(self, query: str, tables: Mapping[str, pl.LazyFrame]) -> pl.LazyFrame:
        key, hit = self._cached(f"sql:{query}")
        if hit is not None:
            return pl.scan_parquet(hit)
        tmp = self._engine.tmp_dir
        paths: dict[str, Path | list[Path]] = {}
        for name, lf in tables.items():
            p = tmp / f"{self.input_id}-{self.step_id}-{name}-{next(_counter)}.parquet"
            lf.sink_parquet(p)
            paths[name] = p
        # Таблицы других входов сценария, на которые ссылается запрос, подставляются сами.
        for name in tables_in(query):
            if name not in paths and name in self._engine.input_paths:
                paths[name] = self._engine.input_paths[name]
        out = tmp / f"{self.input_id}-{self.step_id}-out-{next(_counter)}.parquet"
        self._engine.duck.run(query, paths, out)
        return pl.scan_parquet(self._store(key, out))

    def run_code(
        self,
        lf: pl.LazyFrame,
        code: str,
        *,
        mode: CodeMode = "table",
        frame: CodeFrame = "pandas",
        function: str = "transform",
        timeout: float | None = None,
        pandas_types: PandasTypes = "numpy",
    ) -> pl.LazyFrame:
        ctx = user_context(self._engine, self.node, self.period, self._ip.schema.names)
        if mode == "lazy":
            logs: list[str] = []
            try:
                out = run_lazy(lf, code, self.node, ctx, logs)
            finally:
                for line in logs:
                    self.log(line)
                for w in ctx.warnings:
                    self.warn(w)
            return out
        key, hit = self._cached(f"code:{mode}:{frame}:{pandas_types}:{function}:{code}")
        if hit is not None:
            return pl.scan_parquet(hit)
        tmp = self._engine.tmp_dir
        n = next(_counter)
        src = tmp / f"{self.input_id}-{self.step_id}-code-in-{n}.parquet"
        dst = tmp / f"{self.input_id}-{self.step_id}-code-out-{n}.parquet"
        lf.sink_parquet(src)
        limits = self._engine.duck.limits
        if mode == "table":
            # Оценка памяти: таблица в pandas занимает примерно втрое больше, чем в Arrow.
            meta = pq.ParquetFile(src).metadata
            size = sum(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
            if 3 * size > limits.code_memory // 2:
                raise AgenError(
                    ErrorCode.USER_CODE,
                    f"таблица для кода слишком большая: около {gb(3 * size)} в pandas "
                    f"при лимите {gb(limits.code_memory)}",
                    hint="Выберите режим lazy (ленивый) или batches (по частям), или сузьте окно данных.",
                )
        job = {
            "mode": mode,
            "node": self.node,
            "code": code,
            "frame": frame,
            "pandas_types": pandas_types,
            "input": str(src),
            "output": str(dst),
            "context": context_json(ctx),
        }
        t = timeout if timeout is not None else self._engine.opts.code_timeout
        t0 = time.perf_counter()
        try:
            res = run_job(job, tmp / f"job-{n}.json", timeout=t, memory=limits.code_memory)
        except AgenError as e:
            for line in e.details.get("logs") or []:
                self.log(line)
            for w in e.details.get("warnings") or []:
                self.warn(w)
            raise
        logs, warnings = res.get("logs") or [], res.get("warnings") or []
        for line in logs:
            self.log(line)
        for w in warnings:
            self.warn(w)
        self._engine.code_seconds[self.node] = time.perf_counter() - t0
        src.unlink(missing_ok=True)
        return pl.scan_parquet(self._store(key, dst, {"logs": logs, "warnings": warnings}))
