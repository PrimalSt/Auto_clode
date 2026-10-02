"""Пользовательский код на Python: шаги «Python», наборы и показатели на Python (F-210, F-308).

Код — текст из сценария с функцией: ``transform(df, ctx)`` у шага, ``build(tables, ctx)`` у
набора, ``value(tables, ctx)`` у показателя. Здесь — как этот код компилируется, что
получает и как его результат превращается обратно в таблицу. Режимы шага:

- ``table`` — вся таблица в pandas (или Polars); выполняется в отдельном процессе;
- ``batches`` — порциями pandas по 100 тыс. строк; тоже в отдельном процессе;
- ``lazy`` — ``pl.LazyFrame``: код строит план, выполнение остаётся потоковым.

Отдельный процесс запускает ``isolated.run_job``; ``print`` попадает в журнал, ошибка
показывается с номером строки кода.
"""

from __future__ import annotations

import ast
import contextlib
import io
import linecache
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from autogenerator.contracts import AgenError, DateSpan, ErrorCode, Period

BATCH_ROWS = 100_000


class UserCodeError(AgenError):
    """Ошибка в пользовательском коде: с номером строки и коротким следом вызовов."""

    def __init__(self, message: str, line: int | None = None, trace: str = ""):
        super().__init__(ErrorCode.USER_CODE, message, details={"line": line, "traceback": trace})
        self.line = line
        self.trace = trace


@dataclass
class UserContext:
    """``ctx`` пользовательского кода: отчётный период, окна, замечания в журнал."""

    period: Period
    anchor: date
    windows: dict[str, DateSpan] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)
    node: str = ""
    warnings: list[str] = field(default_factory=list)
    resolver: Callable[[str], DateSpan] | None = None

    def window(self, spec: str) -> DateSpan:
        """Отрезок окна данных: ``ctx.window("previous_period").start``."""
        if spec in self.windows:
            return self.windows[spec]
        if self.resolver is not None:
            return self.resolver(spec)
        known = ", ".join(sorted(self.windows))
        raise ValueError(f"Окно «{spec}» здесь недоступно. Есть: {known}")

    def warn(self, message: str) -> None:
        """Предупреждение в журнал запуска."""
        self.warnings.append(str(message))


# --- компиляция и ошибки --------------------------------------------------------------


def code_filename(node: str) -> str:
    return f"<код {node}>"


def compile_code(code: str, node: str) -> Any:
    filename = code_filename(node)
    try:
        compiled = compile(code, filename, "exec")
    except SyntaxError as e:
        raise UserCodeError(f"синтаксическая ошибка в строке {e.lineno}: {e.msg}", e.lineno) from e
    # Чтобы в следе вызовов были видны строки кода.
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    return compiled


def check_code(code: str, function: str, args: int, node: str) -> list[str]:
    """Проверка без запуска: синтаксис и функция с нужным числом аргументов. Возвращает
    ошибки; синтаксическая ошибка — ``UserCodeError``."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise UserCodeError(f"синтаксическая ошибка в строке {e.lineno}: {e.msg}", e.lineno) from e
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == function:
            got = len(n.args.args) + len(n.args.posonlyargs)
            if got != args and n.args.vararg is None:
                return [f"функция {function} должна принимать {args} аргумента, а принимает {got}"]
            return []
    return [f"в коде нет функции {function}(…)"]


def string_literals(code: str) -> set[str]:
    """Строки в коде: среди них могут быть id столбцов (слабая связь для сверки)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def describe_error(e: BaseException, node: str) -> UserCodeError:
    """Ошибка пользовательского кода: строка кода, тип и текст исключения, след вызовов
    только по коду пользователя."""
    if isinstance(e, UserCodeError):
        return e
    filename = code_filename(node)
    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == filename]
    line = frames[-1].lineno if frames else None
    text = f"{type(e).__name__}: {e}".strip()
    where = f"строка {line}: " if line else ""
    trace = "".join(traceback.format_list(frames)) if frames else ""
    return UserCodeError(where + text, line, trace)


def load_function(code: str, function: str, node: str) -> Callable[..., Any]:
    ns: dict[str, Any] = {"__name__": f"agen_code_{abs(hash(node))}"}
    try:
        exec(compile_code(code, node), ns)
    except UserCodeError:
        raise
    except BaseException as e:
        raise describe_error(e, node) from e
    fn = ns.get(function)
    if not callable(fn):
        raise UserCodeError(f"в коде нет функции {function}(…)")
    return fn


def call(fn: Callable[..., Any], args: tuple[Any, ...], node: str, logs: list[str]) -> Any:
    """Вызвать функцию пользователя; ``print`` — в журнал."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            return fn(*args)
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt | SystemExit):
            raise UserCodeError(f"код вызвал {type(e).__name__}") from e
        raise describe_error(e, node) from e
    finally:
        logs.extend(line for line in buf.getvalue().splitlines() if line.strip())


# --- таблицы туда и обратно -------------------------------------------------------------


def to_frame(table: pa.Table, frame: str, pandas_types: str = "numpy") -> Any:
    """Таблица для кода пользователя: pandas (типы numpy или Arrow) или Polars."""
    df = pl.from_arrow(table)
    assert isinstance(df, pl.DataFrame)
    if frame == "polars":
        return df
    return df.to_pandas(use_pyarrow_extension_array=pandas_types == "arrow")


def to_table(result: Any, what: str) -> pa.Table:
    """Результат кода → таблица Arrow. Именованный индекс pandas становится столбцом."""
    if isinstance(result, pa.Table):
        return result
    if isinstance(result, pl.LazyFrame):
        result = result.collect()
    if isinstance(result, pl.DataFrame):
        return result.to_arrow()
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - pandas ставится вместе с движком
        pd = None
    if pd is not None and isinstance(result, pd.Series):
        result = result.to_frame()
    if pd is not None and isinstance(result, pd.DataFrame):
        df = result
        if df.index.name is not None or any(n is not None for n in (df.index.names or [])):
            df = df.reset_index()
        df.columns = [str(c) for c in df.columns]
        return pa.Table.from_pandas(df, preserve_index=False)
    raise UserCodeError(f"{what} должна вернуть таблицу (pandas или Polars), а вернула {type(result).__name__}")


def _string_types(table: pa.Table) -> pa.Table:
    """Строки — обычные ``large_string``: так их одинаково читают Polars и DuckDB."""
    fields = []
    changed = False
    for f in table.schema:
        if pa.types.is_string(f.type) or pa.types.is_large_string(f.type) or pa.types.is_string_view(f.type):
            fields.append(pa.field(f.name, pa.large_string()))
            changed = changed or not pa.types.is_large_string(f.type)
        elif pa.types.is_dictionary(f.type):
            fields.append(pa.field(f.name, f.type.value_type))
            changed = True
        else:
            fields.append(f)
    return table.cast(pa.schema(fields)) if changed else table


# --- режимы ----------------------------------------------------------------------


def run_table(
    src: Path, out: Path, code: str, node: str, ctx: UserContext, frame: str, pandas_types: str, logs: list[str]
) -> int:
    fn = load_function(code, "transform", node)
    data = to_frame(pq.read_table(src), frame, pandas_types)
    result = call(fn, (data, ctx), node, logs)
    table = _string_types(to_table(result, "функция transform"))
    pq.write_table(table, out, compression="zstd")
    return int(table.num_rows)


def run_batches(
    src: Path, out: Path, code: str, node: str, ctx: UserContext, frame: str, pandas_types: str, logs: list[str]
) -> int:
    fn = load_function(code, "transform", node)
    pf = pq.ParquetFile(src)
    writer: pq.ParquetWriter | None = None
    schema: pa.Schema | None = None
    rows = 0
    try:
        for i, batch in enumerate(pf.iter_batches(batch_size=BATCH_ROWS), start=1):
            data = to_frame(pa.Table.from_batches([batch]), frame, pandas_types)
            table = _string_types(to_table(call(fn, (data, ctx), node, logs), "функция transform"))
            if schema is None:
                schema = table.schema
                writer = pq.ParquetWriter(out, schema, compression="zstd")
            elif table.schema.names != schema.names:
                raise UserCodeError(
                    f"порция {i} вернула другие столбцы: {', '.join(table.schema.names)} "
                    f"вместо {', '.join(schema.names)}"
                )
            else:
                try:
                    table = table.cast(schema)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
                    raise UserCodeError(f"порция {i} вернула другие типы столбцов: {e}") from e
            assert writer is not None
            writer.write_table(table)
            rows += table.num_rows
        if writer is None:
            # Пустой вход: код вызывается на пустой таблице, чтобы узнать столбцы результата.
            empty = pf.schema_arrow.empty_table()
            table = _string_types(
                to_table(call(fn, (to_frame(empty, frame, pandas_types), ctx), node, logs), "transform")
            )
            pq.write_table(table, out, compression="zstd")
    finally:
        if writer is not None:
            writer.close()
    return rows


def run_build(
    tables: Mapping[str, Path],
    out: Path | None,
    code: str,
    node: str,
    ctx: UserContext,
    frame: str,
    pandas_types: str,
    function: str,
    logs: list[str],
) -> Any:
    """Набор (``build``) или показатель (``value``) на Python: функция получает словарь
    таблиц ``{id: DataFrame}``."""
    fn = load_function(code, function, node)
    frames = {k: to_frame(pq.read_table(p), frame, pandas_types) for k, p in tables.items()}
    result = call(fn, (frames, ctx), node, logs)
    if function == "value":
        return scalar(result)
    assert out is not None
    table = _string_types(to_table(result, f"функция {function}"))
    pq.write_table(table, out, compression="zstd")
    return int(table.num_rows)


def scalar(v: Any) -> float | int | None:
    if v is None:
        return None
    if hasattr(v, "item") and callable(v.item):
        with contextlib.suppress(ValueError, TypeError):
            v = v.item()
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError) as e:
        raise UserCodeError(f"функция value должна вернуть число, а вернула {type(v).__name__}") from e
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def run_lazy(lf: pl.LazyFrame, code: str, node: str, ctx: UserContext, logs: list[str]) -> pl.LazyFrame:
    fn = load_function(code, "transform", node)
    result = call(fn, (lf, ctx), node, logs)
    if isinstance(result, pl.DataFrame):
        result = result.lazy()
    if not isinstance(result, pl.LazyFrame):
        raise UserCodeError(
            f"в режиме lazy функция transform должна вернуть pl.LazyFrame, а вернула {type(result).__name__}"
        )
    try:
        result.collect_schema()
    except Exception as e:
        raise describe_error(e, node) from e
    return result
