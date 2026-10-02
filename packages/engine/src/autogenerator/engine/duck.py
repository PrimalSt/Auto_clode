"""DuckDB: запросы SQL и операции над всей таблицей с выгрузкой на диск (ARCHITECTURE.md, раздел 6.4).

Таблицы передаются в DuckDB только через Parquet на диске: ленивый план Polars, отданный
DuckDB напрямую, материализуется целиком в памяти. Результат тоже пишется в Parquet и
читается обратно лениво. У каждого соединения явно заданы ``memory_limit``, ``threads`` и
``temp_directory``: при нехватке памяти DuckDB выгружает промежуточные данные на диск.

Без данных DuckDB проверяет формулы и запросы: по пустым таблицам с нужными столбцами и
типами ``DESCRIBE`` даёт столбцы и типы результата или понятную ошибку.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import duckdb

from autogenerator.contracts import AgenError, ColumnTypes, DType, ErrorCode

from .resources import Limits
from .sqlexpr import query_to_duckdb, to_duckdb

DUCK_TYPES: dict[DType, str] = {
    DType.STRING: "VARCHAR",
    DType.INT: "BIGINT",
    DType.FLOAT: "DOUBLE",
    DType.DATE: "DATE",
    DType.DATETIME: "TIMESTAMP",
    DType.BOOL: "BOOLEAN",
}

_INTS = {
    "BIGINT",
    "INTEGER",
    "SMALLINT",
    "TINYINT",
    "HUGEINT",
    "UBIGINT",
    "UINTEGER",
    "USMALLINT",
    "UTINYINT",
    "UHUGEINT",
}
_counter = itertools.count()


def dtype_of(duck_type: str) -> DType | None:
    t = duck_type.upper()
    if t.startswith("VARCHAR"):
        return DType.STRING
    if t in _INTS:
        return DType.INT
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL"):
        return DType.FLOAT
    if t == "DATE":
        return DType.DATE
    if t.startswith("TIMESTAMP"):
        return DType.DATETIME
    if t == "BOOLEAN":
        return DType.BOOL
    return None


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal_path(p: Path) -> str:
    return "'" + p.resolve().as_posix().replace("'", "''") + "'"


def _message(e: Exception) -> str:
    """Текст ошибки DuckDB без служебного заголовка и с понятным началом."""
    text = str(e).strip()
    text = re.sub(r"^(Binder|Parser|Catalog|Conversion|Invalid Input|Out of Memory) Error:\s*", "", text)
    return text


class Duck:
    """Соединения DuckDB с лимитами одного задания."""

    def __init__(self, temp_dir: Path, limits: Limits | None = None):
        self.temp_dir = temp_dir
        self.limits = limits or Limits.default()

    def connect(self) -> duckdb.DuckDBPyConnection:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(":memory:")
        mb = max(256, self.limits.duckdb_memory >> 20)
        con.execute(f"SET memory_limit = '{mb}MB'")
        con.execute(f"SET threads = {max(1, self.limits.threads)}")
        con.execute(f"SET temp_directory = {_literal_path(self.temp_dir)}")
        con.execute("SET preserve_insertion_order = true")
        return con

    # --- выполнение -----------------------------------------------------------------

    def run(self, query: str, tables: Mapping[str, Path | list[Path]], out: Path) -> Path:
        """Выполнить запрос над Parquet-таблицами и записать результат в ``out``."""
        out.parent.mkdir(parents=True, exist_ok=True)
        con = self.connect()
        try:
            for name, src in tables.items():
                files = src if isinstance(src, list) else [src]
                lst = ", ".join(_literal_path(f) for f in files)
                con.execute(f"CREATE VIEW {quote(name)} AS SELECT * FROM read_parquet([{lst}])")
            sql = query_to_duckdb(query)
            try:
                described = con.execute(f"DESCRIBE {sql}").fetchall()
            except duckdb.Error as e:
                raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в запросе: {_message(e)}") from e
            # Десятичные и 128-битные числа Polars читает неудобно: приводим к DOUBLE и BIGINT.
            casts = []
            for row in described:
                name, t = str(row[0]), str(row[1]).upper()
                if t.startswith("DECIMAL") or t in ("HUGEINT", "UHUGEINT"):
                    casts.append(f"CAST({quote(name)} AS DOUBLE) AS {quote(name)}")
                elif t in ("UBIGINT", "UINTEGER", "USMALLINT", "UTINYINT", "INTEGER", "SMALLINT", "TINYINT"):
                    casts.append(f"CAST({quote(name)} AS BIGINT) AS {quote(name)}")
                elif t.startswith("TIMESTAMP") and t != "TIMESTAMP":
                    casts.append(f"CAST({quote(name)} AS TIMESTAMP) AS {quote(name)}")
            final = f"SELECT * REPLACE ({', '.join(casts)}) FROM ({sql})" if casts else sql
            tmp = out.with_name(f"{out.name}.{next(_counter)}.tmp")
            try:
                con.execute(f"COPY ({final}) TO {_literal_path(tmp)} (FORMAT PARQUET, COMPRESSION zstd)")
            except duckdb.OutOfMemoryException as e:
                raise AgenError(
                    ErrorCode.NODE_FAILED,
                    f"DuckDB не хватило памяти ({_message(e)})",
                    hint="Сузьте окно или уменьшите глубину поиска дубликатов.",
                ) from e
            except duckdb.Error as e:
                raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в запросе: {_message(e)}") from e
            tmp.replace(out)
        finally:
            con.close()
        return out

    def scalar(self, query: str, tables: Mapping[str, Path | list[Path]]) -> Any:
        """Запрос, который возвращает одно значение."""
        con = self.connect()
        try:
            for name, src in tables.items():
                files = src if isinstance(src, list) else [src]
                lst = ", ".join(_literal_path(f) for f in files)
                con.execute(f"CREATE VIEW {quote(name)} AS SELECT * FROM read_parquet([{lst}])")
            try:
                rows = con.execute(query_to_duckdb(query)).fetchall()
                cols = con.description or []
            except duckdb.Error as e:
                raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в запросе: {_message(e)}") from e
        finally:
            con.close()
        if len(cols) != 1 or len(rows) > 1:
            raise AgenError(
                ErrorCode.EXPRESSION,
                f"Запрос показателя должен вернуть одно число, а вернул {len(rows)} строк × {len(cols)} столбцов",
            )
        return rows[0][0] if rows else None

    def small(self, query: str, tables: Mapping[str, Any]) -> Any:
        """Запрос над небольшими таблицами в памяти (Arrow): готовые наборы, строка
        показателей. Возвращает ``pa.Table``."""
        con = duckdb.connect(":memory:")
        try:
            for name, table in tables.items():
                con.register(name, table)
            try:
                return con.execute(query_to_duckdb(query)).to_arrow_table()
            except duckdb.Error as e:
                raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в запросе: {_message(e)}") from e
        finally:
            con.close()

    # --- проверка без данных -----------------------------------------------------------

    def _empty_tables(self, con: duckdb.DuckDBPyConnection, tables: Mapping[str, ColumnTypes]) -> None:
        for name, cols in tables.items():
            defs = ", ".join(f"{quote(c)} {DUCK_TYPES.get(t, 'VARCHAR') if t else 'VARCHAR'}" for c, t in cols.items())
            con.execute(f"CREATE TABLE {quote(name)} ({defs or 'dummy__ INTEGER'})")

    def describe(self, query: str, tables: Mapping[str, ColumnTypes]) -> ColumnTypes:
        """Столбцы и типы результата запроса над пустыми таблицами. Ошибка — ``AgenError``."""
        con = duckdb.connect(":memory:")
        try:
            self._empty_tables(con, tables)
            try:
                rows = con.execute(f"DESCRIBE {query_to_duckdb(query)}").fetchall()
            except duckdb.Error as e:
                raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в запросе: {_message(e)}") from e
        finally:
            con.close()
        return {str(r[0]): dtype_of(str(r[1])) for r in rows}

    def expr_type(self, sql: str, schema: ColumnTypes) -> DType | None:
        """Тип результата формулы над таблицей ``schema``. Ошибка — ``AgenError``."""
        con = duckdb.connect(":memory:")
        try:
            self._empty_tables(con, {"data": schema})
            try:
                rows = con.execute(f"DESCRIBE SELECT ({to_duckdb(sql)}) AS v FROM data").fetchall()
            except duckdb.Error as e:
                raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в формуле «{sql}»: {_message(e)}") from e
        finally:
            con.close()
        return dtype_of(str(rows[0][1]))
