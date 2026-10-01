"""Шаги «SQL» и «Python» (F-210, F-211): свой запрос или код, когда шагов конструктора мало.

Шаг «SQL» выполняет запрос DuckDB к текущей таблице ``data`` и к другим входам сценария по
их ``id``. Шаг «Python» вызывает функцию ``transform(df, ctx)`` в одном из трёх режимов:
``table`` (вся таблица в pandas или Polars, отдельный процесс), ``lazy`` (``pl.LazyFrame``,
выполнение остаётся потоковым) и ``batches`` (порции pandas, отдельный процесс).
"""

from __future__ import annotations

import ast
from typing import Any, Literal

import polars as pl
from pydantic import Field, model_validator

from autogenerator.contracts import (
    AgenError,
    ColumnTypes,
    ErrorCode,
    ExpressionTools,
    Issue,
    IssueLevel,
    SchemaTools,
    StepContext,
    StepPlugin,
)

from .steps import _DTYPES, CastType, _Params

# --- sql ----------------------------------------------------------------------------


class SqlParams(_Params):
    query: str = Field(description="Запрос DuckDB: SELECT … FROM data; другие входы — по их id")


class SqlStep(StepPlugin):
    name = "sql"
    title = "SQL"
    Params = SqlParams
    row_local = False

    def inputs_used(self, params: Any, tools: ExpressionTools) -> list[str]:
        return sorted(tools.tables_in(params.query) - {"data"})

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return tools.query_columns(params.query, "data") or set()

    def reads_all_columns(self, params: Any, tools: ExpressionTools) -> bool:
        return tools.query_columns(params.query, "data") is None

    def check(self, params: Any, tools: ExpressionTools) -> list[Issue]:
        if "data" not in tools.tables_in(params.query):
            return [Issue(level=IssueLevel.ERROR, message="запрос шага должен читать текущую таблицу: FROM data")]
        return []

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        return tools.query_schema(params.query, {"data": schema})

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        return ctx.sql(params.query, {"data": lf})


# --- python -------------------------------------------------------------------------


GROUPWISE_CALLS = {
    "groupby",
    "group_by",
    "drop_duplicates",
    "duplicated",
    "unique",
    "shift",
    "diff",
    "pct_change",
    "rolling",
    "expanding",
    "cumsum",
    "cumcount",
    "rank",
    "sort_values",
    "nlargest",
    "nsmallest",
    "merge",
    "pivot",
    "pivot_table",
}
"""Вызовы, которые в режиме ``batches`` дают неверный результат: они смотрят на соседние
строки, а порция — только часть таблицы."""


class PythonParams(_Params):
    code: str = Field(description="Код с функцией transform(df, ctx)")
    mode: Literal["table", "lazy", "batches"] = Field(
        "table", description="table — вся таблица; lazy — pl.LazyFrame; batches — порциями по 100 тыс. строк"
    )
    frame: Literal["pandas", "polars"] = Field("pandas", description="Что получает функция в режимах table и batches")
    pandas_types: Literal["numpy", "arrow"] = Field(
        "numpy", description="Типы столбцов pandas: привычные numpy или Arrow (экономнее по памяти)"
    )
    uses: list[str] | None = Field(None, description="Столбцы, которые читает код; пусто — все")
    adds: dict[str, CastType | None] | None = Field(None, description="Столбцы, которые код добавляет, и их типы")
    output: dict[str, CastType | None] | None = Field(
        None, description="Все столбцы результата и их типы (если код меняет набор столбцов)"
    )
    timeout: float | None = Field(None, gt=0, description="Сколько секунд код может работать")
    cache: bool = Field(True, description="false — не брать результат из кэша (код читает внешние файлы)")

    @model_validator(mode="after")
    def _check(self) -> PythonParams:
        if self.adds is not None and self.output is not None:
            raise ValueError("задайте adds (добавленные столбцы) или output (все столбцы результата), не оба")
        return self


def _syntax(code: str) -> ast.Module | Issue:
    try:
        return ast.parse(code)
    except SyntaxError as e:
        return Issue(level=IssueLevel.ERROR, message=f"синтаксическая ошибка в строке {e.lineno}: {e.msg}")


class PythonStep(StepPlugin):
    name = "python"
    title = "Python"
    Params = PythonParams
    row_local = False

    def lookback(self, params: Any) -> int | None:
        # Порции обрабатываются независимо — шаг построчный; в остальных режимах коду может
        # понадобиться вся история.
        return 0 if params.mode == "batches" else None

    def cacheable(self, params: Any) -> bool:
        return bool(params.cache)

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.uses or [])

    def reads_all_columns(self, params: Any, tools: ExpressionTools) -> bool:
        return params.uses is None

    def columns_mentioned(self, params: Any) -> set[str]:
        tree = _syntax(params.code)
        if isinstance(tree, Issue):
            return set()
        return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    def check(self, params: Any, tools: ExpressionTools) -> list[Issue]:
        tree = _syntax(params.code)
        if isinstance(tree, Issue):
            return [tree]
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "transform"), None)
        if fn is None:
            return [Issue(level=IssueLevel.ERROR, message="в коде нет функции transform(df, ctx)")]
        args = len(fn.args.posonlyargs) + len(fn.args.args)
        if args != 2 and fn.args.vararg is None:
            return [
                Issue(
                    level=IssueLevel.ERROR,
                    message=f"функция transform должна принимать 2 аргумента (df, ctx), а принимает {args}",
                )
            ]
        issues = []
        if params.mode == "batches":
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in GROUPWISE_CALLS:
                    issues.append(
                        Issue(
                            level=IssueLevel.WARNING,
                            message=f"строка {n.lineno}: .{n.func.attr}(…) в режиме batches считается внутри порции, "
                            "а не по всей таблице; для таких расчётов выберите режим table или lazy",
                        )
                    )
        return issues

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        if params.output is not None:
            out: ColumnTypes = {c: (_DTYPES[t] if t else None) for c, t in params.output.items()}
            for c, t in schema.items():
                if c.startswith("_"):
                    out.setdefault(c, t)
            return out
        if params.adds is not None:
            return {**schema, **{c: (_DTYPES[t] if t else None) for c, t in params.adds.items()}}
        return None

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        out = ctx.run_code(
            lf,
            params.code,
            mode=params.mode,
            frame=params.frame,
            timeout=params.timeout,
            pandas_types=params.pandas_types,
        )
        declared = params.output or params.adds
        if declared:
            got = set(out.collect_schema().names())
            missing = [c for c in declared if c not in got]
            if missing:
                what = "объявленных в output" if params.output else "объявленных в adds"
                raise AgenError(ErrorCode.USER_CODE, f"код не вернул столбцы, {what}: {', '.join(missing)}")
        return out
